from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, Response
from flask_bcrypt import Bcrypt
from datetime import datetime, timedelta
import random
import io
import csv

from config import Config
from db_helper import DBHelper
from prediction_helper import PredictionHelper

app = Flask(__name__)
app.config.from_object(Config);
bcrypt = Bcrypt(app)

# Helper function to check login credentials and active states
def is_logged_in():
    return 'user_id' in session

def is_verified():
    return session.get('is_verified', 0) == 1

# Custom decorator for route protection
def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_logged_in():
            flash("Please sign in to access the system.", "error")
            return redirect(url_for('login'))
        if not is_verified():
            flash("Your phone number is not verified. Please complete verification.", "warning")
            return redirect(url_for('phone_verification'))
        return f(*args, **kwargs)
    return decorated_function

# Session activity timeout filter (15 minutes)
@app.before_request
def check_session_timeout():
    ignored_endpoints = ['static', 'login', 'register', 'index', 'logout', 
                         'phone_verification', 'otp_verify', 'forgot_password', 
                         'reset_password', 'verify_otp_registration', 'verify_otp_general', 
                         'login_request_otp', 'resend_otp']
    
    if request.endpoint in ignored_endpoints:
        return
        
    if 'user_id' in session:
        last_activity_str = session.get('last_activity')
        if last_activity_str:
            last_activity = datetime.fromisoformat(last_activity_str)
            now = datetime.now()
            timeout_duration = timedelta(minutes=Config.SESSION_TIMEOUT_MINUTES)
            
            if now - last_activity > timeout_duration:
                session.clear()
                flash("Your session has expired due to 15 minutes of inactivity. Please login again.", "warning")
                return redirect(url_for('login'))
        
        session['last_activity'] = datetime.now().isoformat()

# ----------------- OTP Verification Logic -----------------

def send_sms_otp(phone_number, otp_code):
    """
    Sends an OTP to the phone number. Attempts Twilio, falls back to mock console output.
    """
    if Config.TWILIO_ACCOUNT_SID and Config.TWILIO_AUTH_TOKEN and Config.TWILIO_FROM_NUMBER:
        try:
            from twilio.rest import Client
            client = Client(Config.TWILIO_ACCOUNT_SID, Config.TWILIO_AUTH_TOKEN)
            client.messages.create(
                body=f"Your Smart Medical Inventory verification code is: {otp_code}. Valid for 5 minutes.",
                from_=Config.TWILIO_FROM_NUMBER,
                to=phone_number
            )
            return True, "SMS sent successfully via Twilio."
        except Exception as e:
            print(f"Twilio SMS gateway failed: {e}. Falling back to console...")
            
    # Mock SMS Console Fallback
    print(f"\n================ MOCK SMS GATEWAY ================")
    print(f"To: {phone_number}")
    print(f"Verification Code: {otp_code}")
    print(f"Sent At: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"==================================================\n")
    return False, f"Mock SMS Code '{otp_code}' generated. Check server console."

def generate_and_save_otp(user_id, phone_number):
    """
    Generates a 6 digit code, expires in 5 minutes, and records in database.
    """
    otp_code = f"{random.randint(100000, 999999)}"
    created_at = datetime.now()
    expires_at = created_at + timedelta(minutes=Config.OTP_EXPIRY_MINUTES)
    
    # Save to DB
    sql = """
        INSERT INTO otp_verifications (user_id, phone_number, otp_code, expires_at)
        VALUES (%s, %s, %s, %s)
    """
    # SQLite uses ISO format strings
    expires_str = expires_at.strftime('%Y-%m-%d %H:%M:%S')
    DBHelper.execute(sql, (user_id, phone_number, otp_code, expires_str))
    
    # Send SMS (Twilio/Console)
    is_real_sms, msg = send_sms_otp(phone_number, otp_code)
    
    if not is_real_sms:
        flash(f"[MOCK SMS] OTP code '{otp_code}' sent to {phone_number} (Console logs).", "info")
    else:
        flash("OTP code has been sent to your phone number.", "success")
        
    return otp_code

# ----------------- Routes -----------------

@app.route('/')
def index():
    if is_logged_in() and is_verified():
        return redirect(url_for('dashboard'))
    return render_template('index.html')

# Register
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        fullname = request.form['fullname']
        email = request.form['email']
        mobile = request.form['mobile']
        role = request.form['role']
        password = request.form['password']
        confirm_password = request.form['confirm_password']
        
        if password != confirm_password:
            flash("Passwords do not match!", "error")
            return redirect(url_for('register'))
            
        # Check if email or phone is already registered
        existing_email = DBHelper.query_one("SELECT * FROM users WHERE email = %s", (email,))
        existing_phone = DBHelper.query_one("SELECT * FROM users WHERE mobile = %s", (mobile,))
        
        if existing_email:
            flash("Email address is already registered.", "error")
            return redirect(url_for('register'))
        if existing_phone:
            flash("Mobile number is already registered.", "error")
            return redirect(url_for('register'))
            
        # Register user with verification state = 0
        hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
        
        user_sql = """
            INSERT INTO users (fullname, email, mobile, role, password, is_verified)
            VALUES (%s, %s, %s, %s, %s, 0)
        """
        user_id = DBHelper.execute(user_sql, (fullname, email, mobile, role, hashed_password))
        
        # Log to audit trail
        DBHelper.log_audit(user_id, f"User created account: {email}. Pending phone verification.")
        
        # Save temp session details
        session['temp_user_id'] = user_id
        session['temp_phone'] = mobile
        
        # Send OTP
        generate_and_save_otp(user_id, mobile)
        
        return redirect(url_for('phone_verification'))
        
    return render_template('register.html')

# Phone Verification Screen
@app.route('/phone_verification')
def phone_verification():
    phone_number = session.get('temp_phone')
    if not phone_number:
        flash("Registration context missing. Please register again.", "error")
        return redirect(url_for('register'))
    return render_template('phone_verification.html', phone_number=phone_number)

# Verify Registration OTP
@app.route('/verify_otp_registration', methods=['POST'])
def verify_otp_registration():
    otp_code = request.form['otp_code']
    user_id = session.get('temp_user_id')
    phone_number = session.get('temp_phone')
    
    if not user_id or not phone_number:
        flash("Verification session timed out. Please register again.", "error")
        return redirect(url_for('register'))
        
    # Query latest active OTP entry
    otp_entry = DBHelper.query_one("""
        SELECT * FROM otp_verifications 
        WHERE user_id = %s AND phone_number = %s AND is_verified = 0 
        ORDER BY created_at DESC LIMIT 1
    """, (user_id, phone_number))
    
    if not otp_entry:
        flash("No active OTP code requests found. Please resend code.", "error")
        return redirect(url_for('phone_verification'))
        
    # Check attempts
    if otp_entry['attempts'] >= Config.OTP_MAX_ATTEMPTS:
        flash("Maximum verification attempts exceeded. Please request a new OTP.", "error")
        return redirect(url_for('phone_verification'))
        
    # Check expiry
    # Parse datetimes (handles string formats from sqlite)
    expires_str = otp_entry['expires_at']
    if isinstance(expires_str, str):
        expires_at = datetime.fromisoformat(expires_str.replace(' ', 'T'))
    else:
        expires_at = expires_str
        
    if datetime.now() > expires_at:
        flash("OTP has expired (5-minute limit). Please request a new one.", "error")
        return redirect(url_for('phone_verification'))
        
    if otp_entry['otp_code'] == otp_code:
        # Match! Activate User
        DBHelper.execute("UPDATE users SET is_verified = 1 WHERE id = %s", (user_id,))
        DBHelper.execute("UPDATE otp_verifications SET is_verified = 1 WHERE id = %s", (otp_entry['id'],))
        
        # Fetch verified user details
        user = DBHelper.query_one("SELECT * FROM users WHERE id = %s", (user_id,))
        
        # Log to Session
        session.clear()
        session['user_id'] = user['id']
        session['fullname'] = user['fullname']
        session['email'] = user['email']
        session['role'] = user['role']
        session['is_verified'] = 1
        session['last_activity'] = datetime.now().isoformat()
        
        DBHelper.log_audit(user['id'], "User successfully verified phone & logged in.")
        flash("Account successfully activated! Welcome to the system.", "success")
        return redirect(url_for('dashboard'))
    else:
        # Increment attempts
        new_attempts = otp_entry['attempts'] + 1
        DBHelper.execute("UPDATE otp_verifications SET attempts = %s WHERE id = %s", (new_attempts, otp_entry['id']))
        remaining = Config.OTP_MAX_ATTEMPTS - new_attempts
        
        if remaining <= 0:
            flash("Verification failed. Maximum attempts reached.", "error")
        else:
            flash(f"Invalid verification code. {remaining} attempt(s) remaining.", "error")
            
        return redirect(url_for('phone_verification'))

# Resend OTP
@app.route('/resend_otp', methods=['POST'])
def resend_otp():
    phone_number = request.form['phone_number']
    user = DBHelper.query_one("SELECT * FROM users WHERE mobile = %s", (phone_number,))
    
    if not user:
        flash("Mobile number is not registered.", "error")
        return redirect(url_for('login'))
        
    generate_and_save_otp(user['id'], phone_number)
    
    # Detect redirects based on verification state
    if user['is_verified'] == 0:
        session['temp_user_id'] = user['id']
        session['temp_phone'] = phone_number
        return redirect(url_for('phone_verification'))
    else:
        # User is verified but resetting or logging in
        return redirect(url_for('otp_verify', phone=phone_number, purpose=session.get('temp_purpose', 'login')))

# Login (Email + Password)
@app.route('/login', methods=['GET', 'POST'])
def login():
    if is_logged_in() and is_verified():
        return redirect(url_for('dashboard'))
        
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        
        user = DBHelper.query_one("SELECT * FROM users WHERE email = %s", (email,))
        
        if user and bcrypt.check_password_hash(user['password'], password):
            # Check phone status
            if user['is_verified'] == 0:
                session['temp_user_id'] = user['id']
                session['temp_phone'] = user['mobile']
                generate_and_save_otp(user['id'], user['mobile'])
                flash("Your account activation is pending. Enter the OTP code sent to your phone.", "warning")
                return redirect(url_for('phone_verification'))
                
            # Logged In Successfully
            session.clear()
            session['user_id'] = user['id']
            session['fullname'] = user['fullname']
            session['email'] = user['email']
            session['role'] = user['role']
            session['is_verified'] = 1
            session['last_activity'] = datetime.now().isoformat()
            
            DBHelper.log_audit(user['id'], "User signed in successfully via Email/Password.")
            return redirect(url_for('dashboard'))
        else:
            flash("Invalid email address or password.", "error")
            return redirect(url_for('login'))
            
    return render_template('login.html')

# Request Login OTP (Mobile + OTP)
@app.route('/login_request_otp', methods=['POST'])
def login_request_otp():
    mobile = request.form['mobile']
    user = DBHelper.query_one("SELECT * FROM users WHERE mobile = %s", (mobile,))
    
    if not user:
        flash("Mobile number is not registered in the system.", "error")
        return redirect(url_for('login'))
        
    # Check if verified account
    if user['is_verified'] == 0:
        session['temp_user_id'] = user['id']
        session['temp_phone'] = user['mobile']
        generate_and_save_otp(user['id'], user['mobile'])
        flash("Activation pending. Please verify your phone number.", "warning")
        return redirect(url_for('phone_verification'))
        
    session['temp_purpose'] = 'login'
    generate_and_save_otp(user['id'], mobile)
    return redirect(url_for('otp_verify', phone=mobile, purpose='login'))

# Display General OTP page
@app.route('/otp_verify')
def otp_verify():
    phone_number = request.args.get('phone')
    purpose = request.args.get('purpose', 'login')
    
    if not phone_number:
        flash("Missing phone number parameter.", "error")
        return redirect(url_for('login'))
        
    return render_template('otp_verify.html', phone_number=phone_number, purpose=purpose)

# Verify General OTP (Login & Reset Password)
@app.route('/verify_otp_general', methods=['POST'])
def verify_otp_general():
    otp_code = request.form['otp_code']
    phone_number = request.form['phone_number']
    purpose = request.form['purpose']
    
    user = DBHelper.query_one("SELECT * FROM users WHERE mobile = %s", (phone_number,))
    if not user:
        flash("User details not found.", "error")
        return redirect(url_for('login'))
        
    otp_entry = DBHelper.query_one("""
        SELECT * FROM otp_verifications 
        WHERE user_id = %s AND phone_number = %s AND is_verified = 0 
        ORDER BY created_at DESC LIMIT 1
    """, (user['id'], phone_number))
    
    if not otp_entry:
        flash("No active OTP request found. Request code again.", "error")
        return redirect(url_for('otp_verify', phone=phone_number, purpose=purpose))
        
    if otp_entry['attempts'] >= Config.OTP_MAX_ATTEMPTS:
        flash("Verification attempts exceeded. Please request a new OTP.", "error")
        return redirect(url_for('otp_verify', phone=phone_number, purpose=purpose))
        
    expires_str = otp_entry['expires_at']
    if isinstance(expires_str, str):
        expires_at = datetime.fromisoformat(expires_str.replace(' ', 'T'))
    else:
        expires_at = expires_str
        
    if datetime.now() > expires_at:
        flash("OTP has expired. Please request a new code.", "error")
        return redirect(url_for('otp_verify', phone=phone_number, purpose=purpose))
        
    if otp_entry['otp_code'] == otp_code:
        # Verify code
        DBHelper.execute("UPDATE otp_verifications SET is_verified = 1 WHERE id = %s", (otp_entry['id'],))
        
        if purpose == 'login':
            session.clear()
            session['user_id'] = user['id']
            session['fullname'] = user['fullname']
            session['email'] = user['email']
            session['role'] = user['role']
            session['is_verified'] = 1
            session['last_activity'] = datetime.now().isoformat()
            
            DBHelper.log_audit(user['id'], "User signed in successfully via Mobile/OTP.")
            flash("Signed in successfully!", "success")
            return redirect(url_for('dashboard'))
            
        elif purpose == 'reset':
            session['reset_user_id'] = user['id']
            flash("OTP verified. Please set a new password.", "success")
            return redirect(url_for('reset_password'))
    else:
        new_attempts = otp_entry['attempts'] + 1
        DBHelper.execute("UPDATE otp_verifications SET attempts = %s WHERE id = %s", (new_attempts, otp_entry['id']))
        remaining = Config.OTP_MAX_ATTEMPTS - new_attempts
        
        if remaining <= 0:
            flash("Verification failed. Maximum attempts reached.", "error")
        else:
            flash(f"Invalid verification code. {remaining} attempt(s) remaining.", "error")
            
        return redirect(url_for('otp_verify', phone=phone_number, purpose=purpose))

# Forgot Password Request
@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        mobile = request.form['mobile']
        user = DBHelper.query_one("SELECT * FROM users WHERE mobile = %s", (mobile,))
        
        if not user:
            flash("Mobile number is not registered.", "error")
            return redirect(url_for('forgot_password'))
            
        session['temp_purpose'] = 'reset'
        generate_and_save_otp(user['id'], mobile)
        return redirect(url_for('otp_verify', phone=mobile, purpose='reset'))
        
    return render_template('forgot_password.html')

# Reset Password
@app.route('/reset_password', methods=['GET', 'POST'])
def reset_password():
    user_id = session.get('reset_user_id')
    if not user_id:
        flash("Unauthorized access. Complete OTP verification first.", "error")
        return redirect(url_for('forgot_password'))
        
    if request.method == 'POST':
        new_password = request.form['password']
        hashed_password = bcrypt.generate_password_hash(new_password).decode('utf-8')
        
        DBHelper.execute("UPDATE users SET password = %s WHERE id = %s", (hashed_password, user_id))
        DBHelper.log_audit(user_id, "User updated account password via OTP recovery.")
        
        session.pop('reset_user_id', None)
        flash("Password successfully reset! Please sign in.", "success")
        return redirect(url_for('login'))
        
    return render_template('reset_password.html')

# Logout
@app.route('/logout')
def logout():
    if 'user_id' in session:
        DBHelper.log_audit(session['user_id'], "User signed out.")
    session.clear()
    flash("Successfully signed out.", "success")
    return redirect(url_for('index'))

# ----------------- Dashboard -----------------

@app.route('/dashboard')
@login_required
def dashboard():
    today_str = '2026-07-19'
    expiring_soon_limit = '2026-08-18' # 30 days
    
    # KPIs
    total_med = DBHelper.query_one("SELECT COUNT(*) as cnt FROM medicines")['cnt']
    total_qty = DBHelper.query_one("SELECT SUM(quantity) as qty FROM medicines")['qty'] or 0
    total_sups = DBHelper.query_one("SELECT COUNT(*) as cnt FROM suppliers")['cnt']
    low_stock = DBHelper.query_one("SELECT COUNT(*) as cnt FROM medicines WHERE quantity <= 15")['cnt']
    
    expired = DBHelper.query_one("SELECT COUNT(*) as cnt FROM medicines WHERE expiry_date < %s", (today_str,))['cnt']
    expiring_soon = DBHelper.query_one("SELECT COUNT(*) as cnt FROM medicines WHERE expiry_date >= %s AND expiry_date <= %s", (today_str, expiring_soon_limit))['cnt']
    
    # Audit Logs
    recent_logs = DBHelper.query_all("""
        SELECT a.*, u.fullname, u.role 
        FROM audit_logs a 
        LEFT JOIN users u ON a.user_id = u.id 
        ORDER BY a.timestamp DESC LIMIT 5
    """)
    
    # Chart 1: Stock levels by Category
    cat_data = DBHelper.query_all("SELECT category, SUM(quantity) as qty FROM medicines GROUP BY category")
    categories_labels = [row['category'] for row in cat_data]
    categories_data = [row['qty'] for row in cat_data]
    
    # Chart 2: Movement Logs last 30 days (Stock In vs Out)
    movement_data = DBHelper.query_all("""
        SELECT DATE(updated_at) as date, SUM(stock_in) as stock_in, SUM(stock_out) as stock_out 
        FROM inventory 
        WHERE updated_at >= '2026-06-19 00:00:00' 
        GROUP BY DATE(updated_at) 
        ORDER BY date ASC
    """)
    
    # Format datetimes to date strings
    movements_dates = []
    movements_in = []
    movements_out = []
    for row in movement_data:
        date_val = row['date']
        # Handle formats
        if isinstance(date_val, datetime):
            date_str = date_val.strftime('%Y-%m-%d')
        else:
            date_str = str(date_val)
        movements_dates.append(date_str)
        movements_in.append(int(row['stock_in'] or 0))
        movements_out.append(int(row['stock_out'] or 0))
        
    # AISuggestions
    suggestions = PredictionHelper.get_restock_suggestions()
    
    # Medicines list that have history for AI selector (at least 2 history rows)
    history_selector_sql = """
        SELECT m.id, m.medicine_name 
        FROM medicines m 
        JOIN (SELECT medicine_id, COUNT(*) as cnt FROM inventory GROUP BY medicine_id HAVING cnt >= 2) h ON m.id = h.medicine_id
    """
    list_medicines_with_history = DBHelper.query_all(history_selector_sql)
    
    return render_template('dashboard.html',
                           total_medicines=total_med,
                           total_stock=total_qty,
                           total_suppliers=total_sups,
                           low_stock_count=low_stock,
                           expired_count=expired,
                           expiring_soon_count=expiring_soon,
                           recent_logs=recent_logs,
                           categories_labels=categories_labels,
                           categories_data=categories_data,
                           movements_dates=movements_dates,
                           movements_in=movements_in,
                           movements_out=movements_out,
                           suggestions=suggestions,
                           list_medicines_with_history=list_medicines_with_history)

# JSON API for Selector Forecasting
@app.route('/api/forecast/<int:medicine_id>')
@login_required
def api_forecast(medicine_id):
    results = PredictionHelper.forecast_stock(medicine_id)
    return jsonify(results)

# JSON API for Medicine details modal
@app.route('/api/medicine/<int:medicine_id>')
@login_required
def api_medicine(medicine_id):
    med = DBHelper.query_one("SELECT * FROM medicines WHERE id = %s", (medicine_id,))
    if not med:
        return jsonify({'error': 'Medicine not found'}), 404
        
    # Check expiry statuses
    today = datetime.strptime('2026-07-19', '%Y-%m-%d').date()
    # Handle string/date conversions
    expiry_val = med['expiry_date']
    if isinstance(expiry_val, str):
        expiry_date = datetime.strptime(expiry_val, '%Y-%m-%d').date()
    else:
        expiry_date = expiry_val
        
    med['is_expired'] = expiry_date < today
    med['is_expiring_soon'] = today <= expiry_date <= (today + timedelta(days=30))
    med['expiry_date'] = expiry_date.strftime('%Y-%m-%d')
    
    # Fetch Supplier contact
    supplier = None
    if med['supplier_id']:
        supplier = DBHelper.query_one("SELECT * FROM suppliers WHERE id = %s", (med['supplier_id'],))
        
    # AI Predictions
    demand_preds = PredictionHelper.predict_demand()
    ai_demand = demand_preds.get(medicine_id, 'Low Demand')
    ai_forecast = PredictionHelper.forecast_stock(medicine_id)
    
    return jsonify({
        'id': med['id'],
        'medicine_name': med['medicine_name'],
        'category': med['category'],
        'batch_no': med['batch_no'],
        'manufacturer': med['manufacturer'],
        'purchase_price': float(med['purchase_price']),
        'selling_price': float(med['selling_price']),
        'quantity': med['quantity'],
        'expiry_date': med['expiry_date'],
        'is_expired': med['is_expired'],
        'is_expiring_soon': med['is_expiring_soon'],
        'supplier': supplier,
        'ai_demand': ai_demand,
        'ai_forecast': ai_forecast
    })

# ----------------- Medicine CRUD -----------------

@app.route('/medicines')
@login_required
def medicines():
    search_query = request.args.get('search', '').strip()
    filter_query = request.args.get('filter', '').strip()
    sort_query = request.args.get('sort', 'name_asc').strip()
    
    # Build query
    sql = """
        SELECT m.*, s.supplier_name 
        FROM medicines m 
        LEFT JOIN suppliers s ON m.supplier_id = s.id
        WHERE 1=1
    """
    params = []
    
    if search_query:
        sql += " AND (m.medicine_name LIKE %s OR m.batch_no LIKE %s OR m.category LIKE %s OR s.supplier_name LIKE %s)"
        like_term = f"%{search_query}%"
        params.extend([like_term, like_term, like_term, like_term])
        
    today_str = '2026-07-19'
    expiring_soon_limit = '2026-08-18'
    
    if filter_query == 'low_stock':
        sql += " AND m.quantity <= 15"
    elif filter_query == 'expired':
        sql += " AND m.expiry_date < %s"
        params.append(today_str)
    elif filter_query == 'expiring_soon':
        sql += " AND m.expiry_date >= %s AND m.expiry_date <= %s"
        params.extend([today_str, expiring_soon_limit])
    elif filter_query == 'high_demand':
        # Get AI high demand IDs
        demand_preds = PredictionHelper.predict_demand()
        high_demand_ids = [m_id for m_id, d in demand_preds.items() if d == 'High Demand']
        if high_demand_ids:
            # Construct dynamic IN clause
            placeholders = ", ".join(["%s"] * len(high_demand_ids))
            sql += f" AND m.id IN ({placeholders})"
            params.extend(high_demand_ids)
        else:
            sql += " AND 1=0" # Force empty result if none
            
    # Sorting
    if sort_query == 'name_desc':
        sql += " ORDER BY m.medicine_name DESC"
    elif sort_query == 'qty_low':
        sql += " ORDER BY m.quantity ASC"
    elif sort_query == 'qty_high':
        sql += " ORDER BY m.quantity DESC"
    elif sort_query == 'expiry_near':
        sql += " ORDER BY m.expiry_date ASC"
    else:
        sql += " ORDER BY m.medicine_name ASC"
        
    raw_medicines = DBHelper.query_all(sql, tuple(params))
    
    # Append calculated values
    today = datetime.strptime(today_str, '%Y-%m-%d').date()
    for med in raw_medicines:
        expiry_val = med['expiry_date']
        if isinstance(expiry_val, str):
            expiry_date = datetime.strptime(expiry_val, '%Y-%m-%d').date()
        else:
            expiry_date = expiry_val
            
        med['is_expired'] = expiry_date < today
        med['is_expiring_soon'] = today <= expiry_date <= (today + timedelta(days=30))
        
    return render_template('medicines.html',
                           medicines=raw_medicines,
                           search_query=search_query,
                           filter_query=filter_query,
                           sort_query=sort_query)

# Add Medicine
@app.route('/medicines/add', methods=['GET', 'POST'])
@login_required
def add_medicine():
    if session.get('role') not in ['Admin', 'Store Manager']:
        flash("Permission denied. Only admins or store managers can register drugs.", "error")
        return redirect(url_for('medicines'))
        
    if request.method == 'POST':
        name = request.form['medicine_name']
        category = request.form['category']
        batch = request.form['batch_no']
        manufacturer = request.form.get('manufacturer', '')
        purchase = float(request.form['purchase_price'])
        selling = float(request.form['selling_price'])
        quantity = int(request.form['quantity'])
        expiry = request.form['expiry_date']
        supplier_id = request.form.get('supplier_id')
        
        # Check unique batch
        existing = DBHelper.query_one("SELECT * FROM medicines WHERE batch_no = %s", (batch,))
        if existing:
            flash(f"Batch code '{batch}' is already in use by another drug.", "error")
            return redirect(url_for('add_medicine'))
            
        sql = """
            INSERT INTO medicines (medicine_name, category, batch_no, manufacturer, purchase_price, selling_price, quantity, expiry_date, supplier_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        med_id = DBHelper.execute(sql, (name, category, batch, manufacturer, purchase, selling, quantity, expiry, supplier_id))
        
        # Record initial stock-in log
        inv_sql = """
            INSERT INTO inventory (medicine_id, stock_in, stock_out, current_stock)
            VALUES (%s, %s, %s, %s)
        """
        DBHelper.execute(inv_sql, (med_id, quantity, 0, quantity))
        
        DBHelper.log_audit(session['user_id'], f"Registered new medicine batch: {name} (Qty: {quantity}, Batch: {batch})")
        flash(f"Medicine '{name}' added successfully!", "success")
        return redirect(url_for('medicines'))
        
    suppliers = DBHelper.query_all("SELECT id, supplier_name FROM suppliers ORDER BY supplier_name")
    return render_template('add_medicine.html', suppliers=suppliers, medicine=None)

# Edit Medicine
@app.route('/medicines/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_medicine(id):
    if session.get('role') not in ['Admin', 'Store Manager']:
        flash("Permission denied. Only admins or store managers can update records.", "error")
        return redirect(url_for('medicines'))
        
    med = DBHelper.query_one("SELECT * FROM medicines WHERE id = %s", (id,))
    if not med:
        flash("Medicine not found.", "error")
        return redirect(url_for('medicines'))
        
    if request.method == 'POST':
        name = request.form['medicine_name']
        category = request.form['category']
        manufacturer = request.form.get('manufacturer', '')
        purchase = float(request.form['purchase_price'])
        selling = float(request.form['selling_price'])
        expiry = request.form['expiry_date']
        supplier_id = request.form.get('supplier_id')
        
        sql = """
            UPDATE medicines 
            SET medicine_name = %s, category = %s, manufacturer = %s, 
                purchase_price = %s, selling_price = %s, expiry_date = %s, supplier_id = %s
            WHERE id = %s
        """
        DBHelper.execute(sql, (name, category, manufacturer, purchase, selling, expiry, supplier_id, id))
        
        DBHelper.log_audit(session['user_id'], f"Updated medicine specs: {name} (ID: {id})")
        flash(f"Medicine details updated successfully!", "success")
        return redirect(url_for('medicines'))
        
    # Parse format for input date field
    expiry_val = med['expiry_date']
    if isinstance(expiry_val, datetime) or hasattr(expiry_val, 'strftime'):
        med['expiry_date'] = expiry_val.strftime('%Y-%m-%d')
        
    suppliers = DBHelper.query_all("SELECT id, supplier_name FROM suppliers ORDER BY supplier_name")
    return render_template('add_medicine.html', suppliers=suppliers, medicine=med)

# Delete Medicine
@app.route('/medicines/delete/<int:id>')
@login_required
def delete_medicine(id):
    if session.get('role') != 'Admin':
        flash("Permission denied. Only Admins can delete records from database.", "error")
        return redirect(url_for('medicines'))
        
    med = DBHelper.query_one("SELECT * FROM medicines WHERE id = %s", (id,))
    if not med:
        flash("Medicine not found.", "error")
        return redirect(url_for('medicines'))
        
    DBHelper.execute("DELETE FROM medicines WHERE id = %s", (id,))
    DBHelper.log_audit(session['user_id'], f"Deleted medicine record: {med['medicine_name']} (Batch: {med['batch_no']})")
    flash(f"Medicine '{med['medicine_name']}' has been deleted.", "success")
    return redirect(url_for('medicines'))

# ----------------- Suppliers CRUD -----------------

@app.route('/suppliers')
@login_required
def suppliers():
    search_query = request.args.get('search', '').strip()
    
    sql = "SELECT * FROM suppliers"
    params = []
    
    if search_query:
        sql += " WHERE supplier_name LIKE %s OR contact_person LIKE %s OR phone LIKE %s OR email LIKE %s"
        term = f"%{search_query}%"
        params.extend([term, term, term, term])
        
    sql += " ORDER BY supplier_name ASC"
    rows = DBHelper.query_all(sql, tuple(params))
    return render_template('suppliers.html', suppliers=rows, search_query=search_query)

@app.route('/suppliers/add', methods=['POST'])
@login_required
def add_supplier():
    if session.get('role') not in ['Admin', 'Store Manager']:
        flash("Permission denied. Only admins or store managers can update logistics.", "error")
        return redirect(url_for('suppliers'))
        
    name = request.form['supplier_name']
    contact = request.form.get('contact_person', '')
    phone = request.form['phone']
    email = request.form.get('email', '')
    address = request.form.get('address', '')
    
    sql = """
        INSERT INTO suppliers (supplier_name, contact_person, phone, email, address)
        VALUES (%s, %s, %s, %s, %s)
    """
    DBHelper.execute(sql, (name, contact, phone, email, address))
    DBHelper.log_audit(session['user_id'], f"Added supplier company: {name}")
    flash(f"Supplier '{name}' registered successfully!", "success")
    return redirect(url_for('suppliers'))

@app.route('/suppliers/edit/<int:id>', methods=['POST'])
@login_required
def edit_supplier(id):
    if session.get('role') not in ['Admin', 'Store Manager']:
        flash("Permission denied.", "error")
        return redirect(url_for('suppliers'))
        
    name = request.form['supplier_name']
    contact = request.form.get('contact_person', '')
    phone = request.form['phone']
    email = request.form.get('email', '')
    address = request.form.get('address', '')
    
    sql = """
        UPDATE suppliers 
        SET supplier_name = %s, contact_person = %s, phone = %s, email = %s, address = %s 
        WHERE id = %s
    """
    DBHelper.execute(sql, (name, contact, phone, email, address, id))
    DBHelper.log_audit(session['user_id'], f"Updated supplier properties: {name} (ID: {id})")
    flash(f"Supplier properties updated successfully!", "success")
    return redirect(url_for('suppliers'))

@app.route('/suppliers/delete/<int:id>')
@login_required
def delete_supplier(id):
    if session.get('role') != 'Admin':
        flash("Permission denied. Only Admins can wipe vendor registries.", "error")
        return redirect(url_for('suppliers'))
        
    sup = DBHelper.query_one("SELECT * FROM suppliers WHERE id = %s", (id,))
    if not sup:
        flash("Supplier not found.", "error")
        return redirect(url_for('suppliers'))
        
    DBHelper.execute("DELETE FROM suppliers WHERE id = %s", (id,))
    DBHelper.log_audit(session['user_id'], f"Removed supplier company: {sup['supplier_name']}")
    flash(f"Supplier '{sup['supplier_name']}' has been removed.", "success")
    return redirect(url_for('suppliers'))

# ----------------- Inventory Tracking -----------------

@app.route('/inventory')
@login_required
def inventory():
    medicines = DBHelper.query_all("SELECT * FROM medicines ORDER BY medicine_name")
    
    # Fetch historical logs
    logs = DBHelper.query_all("""
        SELECT i.*, m.medicine_name, m.category, m.batch_no 
        FROM inventory i 
        JOIN medicines m ON i.medicine_id = m.id 
        ORDER BY i.updated_at DESC
    """)
    return render_template('inventory.html', medicines=medicines, logs=logs)

@app.route('/inventory/adjust', methods=['POST'])
@login_required
def adjust_stock():
    med_id = int(request.form['medicine_id'])
    trans_type = request.form['trans_type']
    qty = int(request.form['quantity'])
    
    med = DBHelper.query_one("SELECT * FROM medicines WHERE id = %s", (med_id,))
    if not med:
        flash("Medicine not found.", "error")
        return redirect(url_for('inventory'))
        
    current_qty = med['quantity']
    
    if trans_type == 'in':
        new_qty = current_qty + qty
        stock_in = qty
        stock_out = 0
        action_msg = f"Restocked drug inventory: {med['medicine_name']} (+{qty} units)"
    else:
        if qty > current_qty:
            flash("Stock deduct quantity exceeds available balance stock!", "error")
            return redirect(url_for('inventory'))
        new_qty = current_qty - qty
        stock_in = 0
        stock_out = qty
        action_msg = f"Dispensed drug inventory: {med['medicine_name']} (-{qty} units)"
        
    # Update medicines count
    DBHelper.execute("UPDATE medicines SET quantity = %s WHERE id = %s", (new_qty, med_id))
    
    # Record transaction log
    inv_sql = """
        INSERT INTO inventory (medicine_id, stock_in, stock_out, current_stock)
        VALUES (%s, %s, %s, %s)
    """
    DBHelper.execute(inv_sql, (med_id, stock_in, stock_out, new_qty))
    
    # Log to audit trail
    DBHelper.log_audit(session['user_id'], action_msg)
    flash("Stock adjustment transaction recorded successfully!", "success")
    return redirect(url_for('inventory'))

# ----------------- Reports & Export -----------------

@app.route('/reports')
@login_required
def reports():
    report_type = request.args.get('type', 'inventory')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    
    report_data = []
    summary_metrics = {}
    
    # Format filters
    query_start = start_date + ' 00:00:00' if start_date else '1970-01-01 00:00:00'
    query_end = end_date + ' 23:59:59' if end_date else '2030-12-31 23:59:59'
    
    if report_type == 'inventory':
        sql = """
            SELECT i.*, m.medicine_name, m.category, m.batch_no 
            FROM inventory i 
            JOIN medicines m ON i.medicine_id = m.id 
            WHERE i.updated_at >= %s AND i.updated_at <= %s 
            ORDER BY i.updated_at DESC
        """
        report_data = DBHelper.query_all(sql, (query_start, query_end))
        
        # Calculate summary metrics
        sum_sql = """
            SELECT SUM(stock_in) as stock_in_total, SUM(stock_out) as stock_out_total 
            FROM inventory 
            WHERE updated_at >= %s AND updated_at <= %s
        """
        summary_metrics = DBHelper.query_one(sum_sql, (query_start, query_end)) or {}
        
    elif report_type == 'supplier':
        # Suppliers don't rely heavily on timestamp, we filter by ID if no dates or query all
        sql = "SELECT * FROM suppliers ORDER BY supplier_name ASC"
        report_data = DBHelper.query_all(sql)
        
    elif report_type == 'audit':
        sql = """
            SELECT a.*, u.fullname, u.role 
            FROM audit_logs a 
            LEFT JOIN users u ON a.user_id = u.id 
            WHERE a.timestamp >= %s AND a.timestamp <= %s 
            ORDER BY a.timestamp DESC
        """
        report_data = DBHelper.query_all(sql, (query_start, query_end))
        
    return render_template('reports.html',
                           report_type=report_type,
                           start_date=start_date,
                           end_date=end_date,
                           report_data=report_data,
                           summary_metrics=summary_metrics)

@app.route('/reports/export')
@login_required
def export_report_csv():
    report_type = request.args.get('type', 'inventory')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    
    query_start = start_date + ' 00:00:00' if start_date else '1970-01-01 00:00:00'
    query_end = end_date + ' 23:59:59' if end_date else '2030-12-31 23:59:59'
    
    si = io.StringIO()
    cw = csv.writer(si)
    
    if report_type == 'inventory':
        sql = """
            SELECT i.*, m.medicine_name, m.category, m.batch_no 
            FROM inventory i 
            JOIN medicines m ON i.medicine_id = m.id 
            WHERE i.updated_at >= %s AND i.updated_at <= %s 
            ORDER BY i.updated_at DESC
        """
        data = DBHelper.query_all(sql, (query_start, query_end))
        
        cw.writerow(['Date & Time', 'Medicine Name', 'Category', 'Batch Number', 'Transaction Type', 'Qty Changed', 'Current Stock Balance'])
        for row in data:
            trans_type = 'Stock In' if row['stock_in'] > 0 else 'Stock Out'
            qty = row['stock_in'] if row['stock_in'] > 0 else row['stock_out']
            cw.writerow([row['updated_at'], row['medicine_name'], row['category'], row['batch_no'], trans_type, qty, row['current_stock']])
            
    elif report_type == 'supplier':
        sql = "SELECT * FROM suppliers ORDER BY supplier_name ASC"
        data = DBHelper.query_all(sql)
        
        cw.writerow(['Supplier ID', 'Company Name', 'Contact Person', 'Phone Number', 'Email', 'Address'])
        for row in data:
            cw.writerow([f"SUP-{row['id']:03d}", row['supplier_name'], row['contact_person'], row['phone'], row['email'], row['address']])
            
    elif report_type == 'audit':
        sql = """
            SELECT a.*, u.fullname, u.role 
            FROM audit_logs a 
            LEFT JOIN users u ON a.user_id = u.id 
            WHERE a.timestamp >= %s AND a.timestamp <= %s 
            ORDER BY a.timestamp DESC
        """
        data = DBHelper.query_all(sql, (query_start, query_end))
        
        cw.writerow(['Timestamp', 'User Account', 'System Role', 'Action Logs'])
        for row in data:
            cw.writerow([row['timestamp'], row['fullname'], row['role'], row['action']])
            
    response = Response(si.getvalue(), mimetype='text/csv')
    filename = f"medical_report_{report_type}_{datetime.now().strftime('%Y%m%d')}.csv"
    response.headers['Content-Disposition'] = f'attachment; filename={filename}'
    
    DBHelper.log_audit(session['user_id'], f"Exported {report_type} reports as CSV Excel sheet.")
    return response

# ----------------- Profile -----------------

@app.route('/profile')
@login_required
def profile():
    user = DBHelper.query_one("SELECT * FROM users WHERE id = %s", (session['user_id'],))
    user_logs = DBHelper.query_all("SELECT * FROM audit_logs WHERE user_id = %s ORDER BY timestamp DESC LIMIT 20", (session['user_id'],))
    return render_template('profile.html', user=user, user_logs=user_logs)

@app.route('/profile/change_password', methods=['POST'])
@login_required
def change_password():
    old_pass = request.form['old_password']
    new_pass = request.form['new_password']
    
    user = DBHelper.query_one("SELECT * FROM users WHERE id = %s", (session['user_id'],))
    
    if bcrypt.check_password_hash(user['password'], old_pass):
        hashed_password = bcrypt.generate_password_hash(new_pass).decode('utf-8')
        DBHelper.execute("UPDATE users SET password = %s WHERE id = %s", (hashed_password, session['user_id']))
        DBHelper.log_audit(session['user_id'], "User modified account password successfully.")
        flash("Password updated successfully!", "success")
    else:
        flash("Current password entered is incorrect.", "error")
        
    return redirect(url_for('profile'))

if __name__ == '__main__':
    # Initialize DB (Build tables & seed mock values if empty)
    DBHelper.init_db()
    
    app.run(host='0.0.0.0', port=5000, debug=True)
