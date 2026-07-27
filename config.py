import os

class Config:
    # Flask Configuration
    SECRET_KEY = os.environ.get('SECRET_KEY', 'super-secret-key-for-medical-inventory-system-102938')
    
    # Session configurations
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = False  # Set to True in production with HTTPS
    
    # Inactivity timeout (15 minutes)
    SESSION_TIMEOUT_MINUTES = 15
    
    # Database Configuration
    DB_TYPE = os.environ.get('DB_TYPE', 'mysql')  # 'mysql' or 'sqlite'
    
    # MySQL Database Connection Settings
    DB_HOST = os.environ.get('DB_HOST', 'localhost')
    DB_USER = os.environ.get('DB_USER', 'root')
    DB_PASSWORD = os.environ.get('DB_PASSWORD', '')
    DB_NAME = os.environ.get('DB_NAME', 'smart_medical_inventory')
    
    # SQLite Configuration (Fallback)
    SQLITE_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database', 'smart_medical_inventory.db')
    
    # Twilio SMS API Configuration (Optional: Mock mode if missing/default)
    TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID', '')
    TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN', '')
    TWILIO_FROM_NUMBER = os.environ.get('TWILIO_FROM_NUMBER', '')
    
    # OTP Configuration
    OTP_EXPIRY_MINUTES = 5
    OTP_MAX_ATTEMPTS = 3
