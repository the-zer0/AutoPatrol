from flask import Flask, render_template, request, redirect, session, jsonify, send_from_directory
from flask_mysqldb import MySQL
import MySQLdb.cursors
import os
import cv2
import pytesseract
import re
import threading
import time

# Set Tesseract path
pytesseract.pytesseract.tesseract_cmd = r'D:\tesseract\tesseract.exe'

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'

# MySQL configuration for XAMPP
app.config['MYSQL_HOST'] = 'localhost'
app.config['MYSQL_USER'] = 'root'
app.config['MYSQL_PASSWORD'] = ''
app.config['MYSQL_DB'] = 'traffic_db'

# Upload folder config
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

mysql = MySQL(app)

# Shared state
detection_state = {
    'signal': 'green',
    'video_processing': False,
    'video_finished': False,
    'current_video': '',
    'latest_plates': set()
}

@app.route('/')
def home():
    return redirect('/login')

@app.route('/login', methods=['GET', 'POST'])
def login():
    msg = ''
    if request.method == 'POST' and 'username' in request.form and 'password' in request.form:
        username = request.form['username']
        password = request.form['password']
        cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
        cursor.execute('SELECT * FROM users WHERE username = %s AND password = %s AND role = %s',
                       (username, password, 'police'))
        account = cursor.fetchone()
        if account:
            session['loggedin'] = True
            session['id'] = account['id']
            session['username'] = account['username']
            session['role'] = account['role']
            return redirect('/police_dashboard')
        else:
            msg = 'Incorrect username/password for police!'
    return render_template('login.html', msg=msg)

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

@app.route('/police_dashboard')
def police_dashboard():
    if 'loggedin' in session and session.get('role') == 'police':
        return render_template('police_dashboard.html', username=session['username'])
    return redirect('/login')

@app.route('/upload_video', methods=['POST'])
def upload_video():
    if 'loggedin' not in session or session.get('role') != 'police':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    if 'video' not in request.files or request.files['video'].filename == '':
        return jsonify({'success': False, 'error': 'No video file provided'})
    
    video = request.files['video']
    filename = video.filename
    save_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    video.save(save_path)

    detection_state.update({
        'current_video': filename,
        'video_processing': True,
        'video_finished': False,
        'latest_plates': set()
    })

    threading.Thread(target=process_video, args=(save_path,)).start()
    return jsonify({'success': True, 'filename': filename})

@app.route('/toggle_signal', methods=['POST'])
def toggle_signal():
    data = request.get_json()
    signal = data.get('signal')
    if signal in ['red', 'green']:
        detection_state['signal'] = signal
        return jsonify({'success': True, 'signal': signal})
    return jsonify({'success': False, 'error': 'Invalid signal'})

@app.route('/detection_status')
def detection_status():
    return jsonify({
        'video_processing': detection_state['video_processing'],
        'video_finished': detection_state['video_finished'],
        'latest_plates': list(detection_state['latest_plates']),
        'signal': detection_state['signal']
    })

def process_video(video_path):
    cap = cv2.VideoCapture(video_path)
    print("[INFO] Video processing started...")

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if detection_state['signal'] == 'red':
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            gray = cv2.bilateralFilter(gray, 11, 17, 17)
            edged = cv2.Canny(gray, 30, 200)

            contours, _ = cv2.findContours(edged.copy(), cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
            contours = sorted(contours, key=cv2.contourArea, reverse=True)[:10]

            for cnt in contours:
                approx = cv2.approxPolyDP(cnt, 0.018 * cv2.arcLength(cnt, True), True)
                if len(approx) == 4:
                    x, y, w, h = cv2.boundingRect(cnt)
                    plate_img = gray[y:y + h, x:x + w]
                    text = pytesseract.image_to_string(plate_img, config='--oem 3 --psm 6')
                    print(f"[DEBUG] OCR Plate Region Text: {text}")

                    plates = re.findall(r'[A-Z]{2}\d{1,2}[A-Z]{1,2}\d{4}', text.replace(" ", "").upper())

                    if plates:
                        try:
                            db = MySQLdb.connect(host="localhost", user="root", passwd="", db="traffic_db")
                            cursor = db.cursor()
                            for plate in plates:
                                if plate not in detection_state['latest_plates']:
                                    cursor.execute("INSERT INTO violations (plate_number, video_name) VALUES (%s, %s)",
                                                   (plate, detection_state['current_video']))
                                    db.commit()
                                    detection_state['latest_plates'].add(plate)
                                    print(f"[INFO] Detected and saved: {plate}")
                        except Exception as e:
                            print(f"[ERROR] DB error: {e}")
                        finally:
                            db.close()
                    break  

        time.sleep(0.05)

    cap.release()
    detection_state['video_processing'] = False
    detection_state['video_finished'] = True
    print("[INFO] Video processing finished.")


@app.route('/search_violation')
def search_violation():
    plate_number = request.args.get('plate_number', '').strip().upper()
    if not plate_number:
        return redirect('/login')

    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute("SELECT plate_number, violation_time FROM violations WHERE plate_number = %s ORDER BY violation_time DESC", (plate_number,))
    violations = cursor.fetchall()

    return render_template('violations_result.html', plate_number=plate_number, violations=violations)

@app.route('/video/<filename>')
def serve_video(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

if __name__ == '__main__':
    app.run(debug=True)
