from fastapi import FastAPI, Request, HTTPException, Form
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
import models, random, string, uvicorn, os

app = FastAPI(title="序號管理系統")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 登入 ──────────────────────────────────────────────────────────────────
@app.post("/api/admin/login")
async def admin_login(username: str = Form(...), password: str = Form(...)):
    db = models.SessionLocal()
    try:
        admin = db.query(models.AdminUser).filter(models.AdminUser.username == username).first()
        if not admin and username == "rg" and password == "123456":
            admin = models.AdminUser(username="rg", hashed_password="123456", is_superuser=True)
            db.add(admin)
            db.commit()
            return {"access_token": "local_token_success", "token_type": "bearer", "is_superuser": True}
        if admin and admin.hashed_password == password:
            return {"access_token": "local_token_success", "token_type": "bearer", "is_superuser": admin.is_superuser}
        raise HTTPException(status_code=401, detail="帳號或密碼錯誤")
    finally:
        db.close()

# ── 統計 ──────────────────────────────────────────────────────────────────
@app.get("/api/admin/stats")
async def get_stats():
    db = models.SessionLocal()
    try:
        total   = db.query(models.License).count()
        unused  = db.query(models.License).filter(models.License.status == "unused").count()
        active  = db.query(models.License).filter(models.License.status == "active").count()
        expired = db.query(models.License).filter(models.License.status.in_(["expired","disabled"])).count()
        return {"total":total,"unused":unused,"active":active,"expired":expired}
    finally:
        db.close()

# ── 序號清單 ──────────────────────────────────────────────────────────────
@app.get("/api/admin/licenses")
async def list_licenses():
    db = models.SessionLocal()
    try:
        licenses = db.query(models.License).order_by(models.License.created_at.desc()).all()
        return [{"id":l.id,"serial_code":l.serial_code,"type":l.type,"status":l.status,
                 "note":l.note,
                 "activation_date":l.activation_date.isoformat() if l.activation_date else None,
                 "expiry_date":l.expiry_date.isoformat() if l.expiry_date else None,
                 "created_at":l.created_at.isoformat() if l.created_at else None,
                 "last_login_ip":l.last_login_ip} for l in licenses]
    finally:
        db.close()

# ── 生成序號 ──────────────────────────────────────────────────────────────
@app.post("/api/admin/generate")
async def generate_batch(count: int, days: int, note: str = ""):
    db = models.SessionLocal()
    try:
        chars = string.ascii_letters + string.digits
        new_codes = []
        for _ in range(count):
            code = "-".join("".join(random.choices(chars, k=4)) for _ in range(3))
            db.add(models.License(serial_code=code, type=f"{days} 天", status="unused", note=note))
            new_codes.append(code)
        db.commit()
        return {"message": f"成功生成 {count} 組序號", "codes": new_codes}
    finally:
        db.close()

# ── 操作（停用/啟用/刪除） ─────────────────────────────────────────────────
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

# ── 序號驗證（給首頁使用） ────────────────────────────────────────────────
# ===== 管理員帳號管理 =====

@app.get("/api/admin/users")
async def list_users():
    db = models.SessionLocal()
    try:
        users = db.query(models.AdminUser).all()
        return [
            {
                "id": u.id,
                "username": u.username,
                "is_superuser": u.is_superuser
            }
            for u in users
        ]
    finally:
        db.close()


@app.post("/api/admin/users/add")
async def add_user(
    username: str = Form(...),
    password: str = Form(...),
    is_superuser: bool = Form(False)
):
    db = models.SessionLocal()
    try:
        exists = db.query(models.AdminUser).filter(
            models.AdminUser.username == username
        ).first()

        if exists:
            raise HTTPException(status_code=400, detail="User already exists")

        user = models.AdminUser(
            username=username,
            hashed_password=password,
            is_superuser=is_superuser
        )

        db.add(user)
        db.commit()

        return {"success": True}
    finally:
        db.close()


@app.post("/api/admin/users/delete")
async def delete_user(user_id: int):
    db = models.SessionLocal()
    try:
        user = db.query(models.AdminUser).filter(
            models.AdminUser.id == user_id
        ).first()

        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        db.delete(user)
        db.commit()

        return {"success": True}
    finally:
        db.close()async def verify_serial(code: str, request: Request):
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
            return {"valid": True, "days_left": days_left,
                    "expiry": lic.expiry_date.strftime("%Y-%m-%d") if lic.expiry_date else "永久"}
        if lic.status == "unused":
            try:
                days = int(lic.type.split(" ")[0])
            except:
                days = 7
            lic.status = "active"
            lic.activation_date = now
            lic.expiry_date = now + timedelta(days=days)
            lic.last_login_ip = request.client.host
            db.commit()
            return {"valid": True, "days_left": days,
                    "expiry": lic.expiry_date.strftime("%Y-%m-%d"), "first_time": True}
        return {"valid": False, "message": "狀態錯誤"}
    finally:
        db.close()

# ── 靜態頁面 ──────────────────────────────────────────────────────────────
script_dir = os.path.dirname(os.path.realpath(__file__))
static_dir = os.path.join(script_dir, "static")

@app.get("/admin.html")
async def get_admin(): return FileResponse(os.path.join(static_dir, "admin.html"))

@app.get("/login.html")
async def get_login(): return FileResponse(os.path.join(static_dir, "login.html"))

@app.get("/")
@app.get("/index.html")
async def get_index(): return FileResponse(os.path.join(static_dir, "index.html"))

if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8001))
    uvicorn.run(app, host="0.0.0.0", port=port)
