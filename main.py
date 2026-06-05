from fastapi import FastAPI, Request, HTTPException, Form
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from datetime import datetime, timedelta
from pydantic import BaseModel
from sqlalchemy import text
import models
import hashlib
import secrets
import random
import string
import uvicorn
import os

app = FastAPI(title="序號管理系統")


def hash_one_time_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()
    return f"{salt}:{digest}"


def verify_one_time_password(password: str, stored_hash: str) -> bool:
    try:
        salt, digest = stored_hash.split(":", 1)
    except ValueError:
        return False
    candidate = hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()
    return secrets.compare_digest(candidate, digest)


def require_admin_password(db, username: str, password: str):
    admin = db.query(models.AdminUser).filter(models.AdminUser.username == username).first()
    if admin and admin.hashed_password == password:
        return True
    raise HTTPException(status_code=401, detail="管理員密碼錯誤")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 自動補資料庫欄位：owner_username
try:
    with models.engine.connect() as conn:
        if models.DATABASE_URL.startswith("sqlite"):
            columns = conn.execute(text("PRAGMA table_info(licenses)")).fetchall()
            has_owner = any(col[1] == "owner_username" for col in columns)
            if not has_owner:
                conn.execute(text("ALTER TABLE licenses ADD COLUMN owner_username VARCHAR"))
        else:
            conn.execute(text("ALTER TABLE licenses ADD COLUMN IF NOT EXISTS owner_username VARCHAR"))
        conn.commit()
except Exception as e:
    print("owner_username migration skipped:", e)


@app.post("/api/admin/login")
async def admin_login(username: str = Form(...), password: str = Form(...)):
    db = models.SessionLocal()
    try:
        admin = db.query(models.AdminUser).filter(models.AdminUser.username == username).first()

        if not admin and username == "rg" and password == "123456":
            admin = models.AdminUser(username="rg", hashed_password="123456", is_superuser=True)
            db.add(admin)
            db.commit()
            return {
                "access_token": "local_token_success",
                "token_type": "bearer",
                "is_superuser": True,
                "username": "rg"
            }

        if admin and admin.hashed_password == password:
            return {
                "access_token": "local_token_success",
                "token_type": "bearer",
                "is_superuser": admin.is_superuser,
                "username": admin.username
            }

        raise HTTPException(status_code=401, detail="帳號或密碼錯誤")
    finally:
        db.close()


@app.get("/api/admin/stats")
async def get_stats(username: str = "rg"):
    db = models.SessionLocal()
    try:
        admin = db.query(models.AdminUser).filter(models.AdminUser.username == username).first()

        q = db.query(models.License)
        if admin and not admin.is_superuser:
            q = q.filter(models.License.owner_username == username)

        total = q.count()
        unused = q.filter(models.License.status == "unused").count()
        active = q.filter(models.License.status == "active").count()
        expired = q.filter(models.License.status.in_(["expired", "disabled"])).count()

        return {"total": total, "unused": unused, "active": active, "expired": expired}
    finally:
        db.close()


@app.get("/api/admin/licenses")
async def list_licenses(username: str = "rg"):
    db = models.SessionLocal()
    try:
        admin = db.query(models.AdminUser).filter(models.AdminUser.username == username).first()

        q = db.query(models.License)
        if admin and not admin.is_superuser:
            q = q.filter(models.License.owner_username == username)

        licenses = q.order_by(models.License.created_at.desc()).all()

        return [
            {
                "id": l.id,
                "serial_code": l.serial_code,
                "type": l.type,
                "status": l.status,
                "note": l.note,
                "activation_date": l.activation_date.isoformat() if l.activation_date else None,
                "expiry_date": l.expiry_date.isoformat() if l.expiry_date else None,
                "created_at": l.created_at.isoformat() if l.created_at else None,
                "last_login_ip": l.last_login_ip,
                "owner_username": l.owner_username
            }
            for l in licenses
        ]
    finally:
        db.close()


@app.post("/api/admin/generate")
async def generate_batch(
    count: int,
    days: int,
    note: str = "",
    username: str = Form("rg")
):
    db = models.SessionLocal()
    try:
        chars = string.ascii_letters + string.digits
        new_codes = []

        for _ in range(count):
            code = "-".join("".join(random.choices(chars, k=4)) for _ in range(3))

            db.add(models.License(
                serial_code=code,
                type=f"{days} 天",
                status="unused",
                note=note,
                owner_username=username
            ))

            new_codes.append(code)

        db.commit()
        return {"message": f"成功生成 {count} 組序號", "codes": new_codes}
    finally:
        db.close()


@app.post("/api/admin/one-time/generate")
async def generate_one_time_accounts(
    count: int,
    note: str = "",
    username: str = Form(...),
    admin_password: str = Form(...)
):
    db = models.SessionLocal()
    try:
        require_admin_password(db, username, admin_password)

        count = max(1, min(count, 200))
        new_accounts = []

        for _ in range(count):
            while True:
                account_name = "FT-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
                exists = db.query(models.OneTimeAccount).filter(models.OneTimeAccount.username == account_name).first()
                if not exists:
                    break

            password = "".join(random.choices(string.ascii_letters + string.digits, k=8))
            db.add(models.OneTimeAccount(
                username=account_name,
                password_hash=hash_one_time_password(password),
                status="unused",
                note=note,
                owner_username=username
            ))
            new_accounts.append({"username": account_name, "password": password})

        db.commit()
        return {"message": f"成功生成 {count} 組一次性登入帳號", "accounts": new_accounts}
    finally:
        db.close()


@app.get("/api/admin/users")
async def list_admin_users():
    db = models.SessionLocal()
    try:
        users = db.query(models.AdminUser).all()
        return [
            {"id": u.id, "username": u.username, "is_superuser": u.is_superuser}
            for u in users
        ]
    finally:
        db.close()


@app.post("/api/admin/users/add")
async def add_admin_user(
    username: str = Form(...),
    password: str = Form(...),
    is_superuser: bool = Form(False)
):
    db = models.SessionLocal()
    try:
        exists = db.query(models.AdminUser).filter(models.AdminUser.username == username).first()
        if exists:
            raise HTTPException(status_code=400, detail="帳號已存在")

        db.add(models.AdminUser(
            username=username,
            hashed_password=password,
            is_superuser=is_superuser
        ))

        db.commit()
        return {"message": "新增帳號成功"}
    finally:
        db.close()


@app.post("/api/admin/users/delete")
async def delete_admin_user(user_id: int):
    db = models.SessionLocal()
    try:
        user = db.query(models.AdminUser).filter(models.AdminUser.id == user_id).first()

        if not user:
            raise HTTPException(status_code=404, detail="帳號不存在")

        db.delete(user)
        db.commit()

        return {"message": "刪除帳號成功"}
    finally:
        db.close()


@app.post("/api/admin/action")
async def take_action(serial_id: int, action: str, days: int = None):
    db = models.SessionLocal()
    try:
        lic = db.query(models.License).filter(models.License.id == serial_id).first()

        if not lic:
            raise HTTPException(status_code=404, detail="序號不存在")

        if action == "disable":
            lic.status = "disabled"
        elif action == "enable":
            lic.status = "active" if lic.activation_date else "unused"
        elif action == "delete":
            db.delete(lic)
        elif action == "update_days" and days is not None:
            lic.type = f"{days} 天"
            if lic.status == "active" and lic.activation_date:
                lic.expiry_date = lic.activation_date + timedelta(days=days)

        db.commit()
        return {"message": "操作成功"}
    finally:
        db.close()


@app.get("/api/verify")
async def verify_serial(code: str, request: Request):
    db = models.SessionLocal()
    try:
        lic = db.query(models.License).filter(models.License.serial_code == code).first()

        if not lic:
            return {"valid": False, "message": "序號無效"}

        if lic.status == "disabled":
            return {"valid": False, "message": "此序號已被停用"}

        now = datetime.utcnow()

        if lic.status == "active":
            if lic.expiry_date and now > lic.expiry_date:
                lic.status = "expired"
                db.commit()
                return {"valid": False, "message": f"序號已於 {lic.expiry_date.strftime('%Y-%m-%d')} 過期"}

            days_left = (lic.expiry_date - now).days + 1 if lic.expiry_date else None

            return {
                "valid": True,
                "days_left": days_left,
                "expiry": lic.expiry_date.strftime("%Y-%m-%d") if lic.expiry_date else "永久"
            }

        if lic.status == "unused":
            try:
                days = int(lic.type.split(" ")[0])
            except Exception:
                days = 7

            lic.status = "active"
            lic.activation_date = now
            lic.expiry_date = now + timedelta(days=days)
            lic.last_login_ip = request.client.host

            db.commit()

            return {
                "valid": True,
                "days_left": days,
                "expiry": lic.expiry_date.strftime("%Y-%m-%d"),
                "first_time": True
            }

        return {"valid": False, "message": "狀態錯誤"}
    finally:
        db.close()


@app.post("/api/one-time-login")
async def one_time_login(request: Request, username: str = Form(...), password: str = Form(...)):
    db = models.SessionLocal()
    try:
        account = db.query(models.OneTimeAccount).filter(models.OneTimeAccount.username == username).first()

        if not account or not account.password_hash or not verify_one_time_password(password, account.password_hash):
            return {"valid": False, "message": "一次性帳號或密碼錯誤"}

        if account.status == "disabled":
            return {"valid": False, "message": "此一次性帳號已停用"}

        if account.status == "used" or account.used_at:
            return {"valid": False, "message": "此一次性帳號已使用，無法再次登入"}

        account.status = "used"
        account.used_at = datetime.utcnow()
        account.used_ip = request.client.host
        db.commit()

        return {
            "valid": True,
            "message": "一次性登入成功",
            "username": account.username,
            "used_at": account.used_at.isoformat()
        }
    finally:
        db.close()


class ImportSerial(BaseModel):
    code: str
    days: int = 30
    note: str = ""


@app.post("/api/admin/import")
async def import_serial(data: ImportSerial):
    db = models.SessionLocal()
    try:
        existing = db.query(models.License).filter(models.License.serial_code == data.code).first()

        if existing:
            return {"message": "已存在", "skipped": True}

        db.add(models.License(
            serial_code=data.code,
            type=f"{data.days} 天",
            status="unused",
            note=data.note,
            owner_username="gt5889"
        ))

        db.commit()
        return {"message": "匯入成功", "code": data.code}
    finally:
        db.close()


script_dir = os.path.dirname(os.path.realpath(__file__))
static_dir = script_dir


@app.get("/admin.html")
async def get_admin():
    return FileResponse(os.path.join(static_dir, "admin.html"))


@app.get("/login.html")
async def get_login():
    return FileResponse(os.path.join(static_dir, "login.html"))


@app.get("/")
@app.get("/index.html")
async def get_index():
    return FileResponse(os.path.join(static_dir, "index.html"))



@app.post("/api/admin/fix-owner-to-gt5889")
async def fix_owner_to_gt5889():
    db = models.SessionLocal()
    try:
        count = db.query(models.License).update({
            models.License.owner_username: "gt5889"
        })
        db.commit()
        return {"message": f"已將 {count} 組序號指定給 gt5889"}
    finally:
        db.close()
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8001))
    uvicorn.run(app, host="0.0.0.0", port=port)

