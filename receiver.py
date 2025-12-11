from flask import Flask, request, jsonify, render_template_string
import os
from datetime import datetime
from io import BytesIO
from PIL import Image
import subprocess
import tempfile
import hashlib
import json
import threading
import time
import uuid
import logging
import sys

try:
	from flask_cors import CORS
	HAS_CORS = True
except ImportError:
	HAS_CORS = False

SAVE_DIR = r"C:\Users\kmavillanosa\Pictures\IPHONE"  # <-- change this
CHECKSUM_DB_PATH = os.path.join(SAVE_DIR, '.checksums.json')
CHUNK_SIZE = 1024 * 1024  # 1MB chunks for streaming
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
LOG_FILE = os.path.join(LOG_DIR, 'receiver.log')

# Create logs directory if it doesn't exist
os.makedirs(LOG_DIR, exist_ok=True)

# Configure logging
logging.basicConfig(
	level=logging.INFO,
	format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
	handlers=[
		logging.FileHandler(LOG_FILE, encoding='utf-8'),
		logging.StreamHandler(sys.stdout)  # Also log to console
	]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
if HAS_CORS:
	CORS(app)  # Enable CORS for progress tracking
else:
	# Add simple CORS headers manually
	@app.after_request
	def after_request(response):
		response.headers.add('Access-Control-Allow-Origin', '*')
		response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
		response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
		return response

os.makedirs(SAVE_DIR, exist_ok=True)

# Progress tracking: session_id -> {bytes_received, total_bytes, status, file_path}
upload_progress = {}
progress_lock = threading.Lock()

# HTML template for the web UI
UI_HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
	<meta charset="UTF-8">
	<meta name="viewport" content="width=device-width, initial-scale=1.0">
	<title>Flask Backup Receiver - Live Logs</title>
	<style>
		* {
			margin: 0;
			padding: 0;
			box-sizing: border-box;
		}
		
		body {
			font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
			background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
			min-height: 100vh;
			padding: 20px;
		}
		
		.container {
			max-width: 1400px;
			margin: 0 auto;
			background: white;
			border-radius: 12px;
			box-shadow: 0 10px 40px rgba(0,0,0,0.2);
			overflow: hidden;
		}
		
		.header {
			background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
			color: white;
			padding: 30px;
			text-align: center;
		}
		
		.header h1 {
			font-size: 2.5em;
			margin-bottom: 10px;
		}
		
		.header p {
			opacity: 0.9;
			font-size: 1.1em;
		}
		
		.stats-bar {
			background: #f8f9fa;
			padding: 20px 30px;
			display: flex;
			justify-content: space-around;
			flex-wrap: wrap;
			border-bottom: 2px solid #e9ecef;
		}
		
		.stat-item {
			text-align: center;
			padding: 10px 20px;
		}
		
		.stat-label {
			font-size: 0.9em;
			color: #6c757d;
			margin-bottom: 5px;
		}
		
		.stat-value {
			font-size: 1.8em;
			font-weight: bold;
			color: #667eea;
		}
		
		.controls {
			padding: 20px 30px;
			background: #f8f9fa;
			display: flex;
			justify-content: space-between;
			align-items: center;
			flex-wrap: wrap;
			gap: 15px;
			border-bottom: 2px solid #e9ecef;
		}
		
		.btn {
			padding: 10px 20px;
			border: none;
			border-radius: 6px;
			cursor: pointer;
			font-size: 1em;
			font-weight: 600;
			transition: all 0.3s;
		}
		
		.btn-primary {
			background: #667eea;
			color: white;
		}
		
		.btn-primary:hover {
			background: #5568d3;
			transform: translateY(-2px);
		}
		
		.btn-secondary {
			background: #6c757d;
			color: white;
		}
		
		.btn-secondary:hover {
			background: #5a6268;
		}
		
		.btn-success {
			background: #28a745;
			color: white;
		}
		
		.btn-success:hover {
			background: #218838;
		}
		
		.status-indicator {
			display: inline-block;
			width: 12px;
			height: 12px;
			border-radius: 50%;
			margin-right: 8px;
		}
		
		.status-active {
			background: #28a745;
			box-shadow: 0 0 10px #28a745;
		}
		
		.status-paused {
			background: #ffc107;
		}
		
		.logs-container {
			padding: 20px 30px;
			max-height: 600px;
			overflow-y: auto;
			background: #1e1e1e;
		}
		
		.logs {
			font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
			font-size: 0.9em;
			line-height: 1.6;
		}
		
		.log-entry {
			padding: 8px 12px;
			margin-bottom: 4px;
			border-left: 3px solid transparent;
			word-wrap: break-word;
			transition: background 0.2s;
		}
		
		.log-entry:hover {
			background: rgba(255, 255, 255, 0.05);
		}
		
		.log-entry.info {
			color: #d4d4d4;
			border-left-color: #007acc;
		}
		
		.log-entry.debug {
			color: #9cdcfe;
			border-left-color: #4ec9b0;
		}
		
		.log-entry.warning {
			color: #dcdcaa;
			border-left-color: #ffc107;
		}
		
		.log-entry.error {
			color: #f48771;
			border-left-color: #f14c4c;
			background: rgba(241, 76, 76, 0.1);
		}
		
		.log-time {
			color: #858585;
			margin-right: 10px;
		}
		
		.log-level {
			font-weight: bold;
			margin-right: 10px;
			text-transform: uppercase;
		}
		
		.empty-logs {
			text-align: center;
			color: #858585;
			padding: 40px;
			font-size: 1.1em;
		}
		
		.loading {
			text-align: center;
			color: #858585;
			padding: 20px;
		}
		
		@keyframes pulse {
			0%, 100% { opacity: 1; }
			50% { opacity: 0.5; }
		}
		
		.loading::after {
			content: '...';
			animation: pulse 1.5s infinite;
		}
	</style>
</head>
<body>
	<div class="container">
		<div class="header">
			<h1>📱 Flask Backup Receiver</h1>
			<p>Live Activity Monitor</p>
		</div>
		
		<div class="stats-bar">
			<div class="stat-item">
				<div class="stat-label">Active Uploads</div>
				<div class="stat-value" id="active-uploads">0</div>
			</div>
			<div class="stat-item">
				<div class="stat-label">Total Sessions</div>
				<div class="stat-value" id="total-sessions">0</div>
			</div>
			<div class="stat-item">
				<div class="stat-label">Log File Size</div>
				<div class="stat-value" id="log-size">0 KB</div>
			</div>
		</div>
		
		<div class="controls">
			<div>
				<button class="btn btn-primary" onclick="toggleAutoRefresh()">
					<span id="status-indicator" class="status-indicator status-active"></span>
					<span id="refresh-status">Auto-refresh: ON</span>
				</button>
				<button class="btn btn-secondary" onclick="refreshLogs()">Refresh Now</button>
				<button class="btn btn-success" onclick="clearLogs()">Clear Display</button>
			</div>
			<div>
				<label>
					Lines to show:
					<select id="lines-select" onchange="updateLines()">
						<option value="50">50</option>
						<option value="100" selected>100</option>
						<option value="200">200</option>
						<option value="500">500</option>
					</select>
				</label>
			</div>
		</div>
		
		<div class="logs-container">
			<div id="logs" class="logs">
				<div class="loading">Loading logs</div>
			</div>
		</div>
	</div>
	
	<script>
		let autoRefresh = true;
		let refreshInterval = null;
		let currentLines = 100;
		let lastLogCount = 0;
		let lastLogHash = '';
		
		function formatBytes(bytes) {
			if (bytes === 0) return '0 B';
			const k = 1024;
			const sizes = ['B', 'KB', 'MB', 'GB'];
			const i = Math.floor(Math.log(bytes) / Math.log(k));
			return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
		}
		
		function parseLogEntry(logLine) {
			// Parse log format: "2024-01-01 12:00:00,000 - root - INFO - message"
			const match = logLine.match(/^(\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2},\\d{3}) - [^-]+ - (\\w+) - (.+)$/);
			if (match) {
				return {
					time: match[1],
					level: match[2].toLowerCase(),
					message: match[3]
				};
			}
			// Fallback for non-standard log lines
			return {
				time: '',
				level: 'info',
				message: logLine
			};
		}
		
		function scrollToBottom(container) {
			// Use requestAnimationFrame to ensure DOM is updated before scrolling
			requestAnimationFrame(() => {
				container.scrollTop = container.scrollHeight;
			});
		}
		
		function renderLogs(logs) {
			const container = document.getElementById('logs');
			
			if (!logs || logs.length === 0) {
				container.innerHTML = '<div class="empty-logs">No logs available</div>';
				lastLogCount = 0;
				lastLogHash = '';
				return;
			}
			
			// Create a hash of the last log entry to detect new logs
			const currentLogHash = logs.length > 0 ? logs[logs.length - 1].trim() : '';
			const hasNewLogs = logs.length > lastLogCount || (logs.length > 0 && currentLogHash !== lastLogHash);
			
			// Always update if there are new logs or count changed
			if (hasNewLogs || logs.length !== lastLogCount) {
				container.innerHTML = logs.map(log => {
					const entry = parseLogEntry(log.trim());
					return `<div class="log-entry ${entry.level}">
						<span class="log-time">${entry.time || ''}</span>
						<span class="log-level">${entry.level}</span>
						<span class="log-message">${escapeHtml(entry.message)}</span>
					</div>`;
				}).join('');
				
				lastLogCount = logs.length;
				lastLogHash = currentLogHash;
				
				// Always scroll to bottom when logs are updated
				scrollToBottom(container);
			}
		}
		
		function escapeHtml(text) {
			const div = document.createElement('div');
			div.textContent = text;
			return div.innerHTML;
		}
		
		async function fetchLogs() {
			try {
				const response = await fetch(`/api/logs?lines=${currentLines}`);
				const data = await response.json();
				
				if (data.success) {
					renderLogs(data.logs);
				} else {
					console.error('Error fetching logs:', data.error);
				}
			} catch (error) {
				console.error('Error fetching logs:', error);
			}
		}
		
		async function fetchStats() {
			try {
				const response = await fetch('/api/stats');
				const data = await response.json();
				
				if (data.success) {
					document.getElementById('active-uploads').textContent = data.active_uploads;
					document.getElementById('total-sessions').textContent = data.total_sessions;
					document.getElementById('log-size').textContent = formatBytes(data.log_file_size);
				}
			} catch (error) {
				console.error('Error fetching stats:', error);
			}
		}
		
		function refreshLogs() {
			fetchLogs();
			fetchStats();
		}
		
		function toggleAutoRefresh() {
			autoRefresh = !autoRefresh;
			const indicator = document.getElementById('status-indicator');
			const status = document.getElementById('refresh-status');
			
			if (autoRefresh) {
				indicator.className = 'status-indicator status-active';
				status.textContent = 'Auto-refresh: ON';
				startAutoRefresh();
			} else {
				indicator.className = 'status-indicator status-paused';
				status.textContent = 'Auto-refresh: OFF';
				stopAutoRefresh();
			}
		}
		
		function startAutoRefresh() {
			stopAutoRefresh();
			refreshInterval = setInterval(() => {
				refreshLogs();
			}, 2000); // Refresh every 2 seconds
		}
		
		function stopAutoRefresh() {
			if (refreshInterval) {
				clearInterval(refreshInterval);
				refreshInterval = null;
			}
		}
		
		function clearLogs() {
			document.getElementById('logs').innerHTML = '<div class="empty-logs">Logs cleared</div>';
			lastLogCount = 0;
		}
		
		function updateLines() {
			currentLines = parseInt(document.getElementById('lines-select').value);
			fetchLogs();
		}
		
		// Initialize
		refreshLogs();
		startAutoRefresh();
		
		// Refresh stats every 5 seconds
		setInterval(fetchStats, 5000);
	</script>
</body>
</html>'''

def load_checksum_db():
	"""
	Load the checksum database from disk.
	Returns a dictionary mappi	ng checksum -> file path.
	"""
	if os.path.exists(CHECKSUM_DB_PATH):
		try:
			with open(CHECKSUM_DB_PATH, 'r', encoding='utf-8') as f:
				return json.load(f)
		except Exception as e:
			logger.warning(f"Could not load checksum database: {e}")
			return {}
	return {}

def save_checksum_db(checksum_db):
	"""
	Save the checksum database to disk.
	"""
	try:
		with open(CHECKSUM_DB_PATH, 'w', encoding='utf-8') as f:
			json.dump(checksum_db, f, indent=2)
	except Exception as e:
		logger.warning(f"Could not save checksum database: {e}")

def calculate_checksum_streaming(file_stream, total_size=None, progress_callback=None):
	"""
	Calculate SHA256 checksum of file data using streaming.
	Returns the hexadecimal digest string.
	"""
	sha256 = hashlib.sha256()
	bytes_read = 0
	
	while True:
		chunk = file_stream.read(CHUNK_SIZE)
		if not chunk:
			break
		sha256.update(chunk)
		bytes_read += len(chunk)
		
		if progress_callback and total_size:
			progress_callback(bytes_read, total_size)
	
	return sha256.hexdigest()

def calculate_checksum(data):
	"""
	Calculate SHA256 checksum of file data (for small files).
	Returns the hexadecimal digest string.
	"""
	return hashlib.sha256(data).hexdigest()

def is_duplicate(checksum, checksum_db):
	"""
	Check if a file with the given checksum already exists.
	Returns (True, existing_path) if duplicate found, (False, None) otherwise.
	"""
	if checksum in checksum_db:
		existing_path = checksum_db[checksum]
		# Verify the file still exists
		if os.path.exists(existing_path):
			return True, existing_path
		else:
			# File was deleted, remove from database
			del checksum_db[checksum]
			save_checksum_db(checksum_db)
	return False, None

def get_image_date(image_data):
	"""
	Extract the date from image data.
	First tries to get EXIF date, then falls back to current date.
	Returns a datetime object.
	"""
	try:
		image = Image.open(BytesIO(image_data))
		
		# Try to get EXIF data (support both old and new PIL versions)
		exif = None
		if hasattr(image, 'getexif'):
			# Newer PIL version (Pillow 8.0+)
			exif = image.getexif()
		elif hasattr(image, '_getexif') and image._getexif() is not None:
			# Older PIL version
			exif = image._getexif()
		
		if exif is not None:
			# Look for DateTimeOriginal (tag 36867), DateTimeDigitized (tag 36868), or DateTime (tag 306)
			date_tags = [36867, 36868, 306]  # DateTimeOriginal, DateTimeDigitized, DateTime
			
			for tag_id in date_tags:
				date_str = None
				if hasattr(exif, 'get'):
					# Newer PIL version - exif is a dict-like object
					date_str = exif.get(tag_id)
				elif tag_id in exif:
					# Older PIL version - exif is a dict
					date_str = exif[tag_id]
				
				if date_str:
					try:
						# EXIF date format: "YYYY:MM:DD HH:MM:SS"
						date_obj = datetime.strptime(str(date_str), "%Y:%m:%d %H:%M:%S")
						return date_obj
					except (ValueError, TypeError):
						continue
		
		# If no EXIF date found, return current date
		return datetime.now()
	except Exception as e:
		logger.warning(f"Could not extract date from image: {e}")
		# Fall back to current date if image processing fails
		return datetime.now()

def get_year_folder_path(year):
	"""
	Get the path for a year folder, creating it if necessary.
	"""
	year_path = os.path.join(SAVE_DIR, str(year))
	os.makedirs(year_path, exist_ok=True)
	return year_path

def check_ffmpeg_available():
	"""
	Check if ffmpeg is available in the system PATH.
	Returns True if available, False otherwise.
	"""
	try:
		result = subprocess.run(
			['ffmpeg', '-version'],
			capture_output=True,
			text=True,
			timeout=5
		)
		return result.returncode == 0
	except (FileNotFoundError, subprocess.TimeoutExpired):
		return False
	except Exception:
		return False

def convert_quicktime_to_mp4(input_path, output_path, session_id=None):
	"""
	Convert a .quicktime file to .mp4 using ffmpeg with optimized settings for speed.
	Returns True if conversion succeeds, False otherwise.
	"""
	# Check if ffmpeg is available before attempting conversion
	if not check_ffmpeg_available():
		logger.error("ffmpeg not found. Please install ffmpeg to convert video files.")
		if session_id:
			with progress_lock:
				if session_id in upload_progress:
					upload_progress[session_id]['status'] = 'ffmpeg_not_found'
					upload_progress[session_id]['error'] = 'ffmpeg not found. Please install ffmpeg to convert video files.'
		return False
	
	try:
		# Use ffmpeg with fast preset for better performance on strong hardware
		# -i: input file
		# -c:v libx264: video codec
		# -c:a aac: audio codec
		# -preset fast: faster encoding (good for strong hardware)
		# -crf 23: quality (lower is better, 23 is default)
		# -movflags +faststart: optimize for web streaming
		# -threads 0: use all available CPU threads
		# -y: overwrite output file if it exists
		result = subprocess.run(
			['ffmpeg', '-i', input_path, '-c:v', 'libx264', '-c:a', 'aac', 
			 '-preset', 'fast', '-crf', '23', '-movflags', '+faststart', 
			 '-threads', '0', '-y', output_path],
			capture_output=True,
			text=True,
			timeout=600  # 10 minute timeout for large files
		)
		
		if result.returncode == 0:
			logger.info(f"Successfully converted {input_path} to {output_path}")
			if session_id:
				with progress_lock:
					if session_id in upload_progress:
						upload_progress[session_id]['status'] = 'converted'
			return True
		else:
			logger.error(f"FFmpeg conversion failed: {result.stderr}")
			if session_id:
				with progress_lock:
					if session_id in upload_progress:
						upload_progress[session_id]['status'] = 'conversion_failed'
			return False
	except subprocess.TimeoutExpired:
		logger.error(f"FFmpeg conversion timed out for {input_path}")
		return False
	except FileNotFoundError:
		logger.error("ffmpeg not found. Please install ffmpeg to convert video files.")
		return False
	except Exception as e:
		logger.error(f"Error converting quicktime to mp4: {e}", exc_info=True)
		return False

@app.route("/", methods=["GET"])
def index():
	"""
	Serve the web UI for viewing logs.
	"""
	return render_template_string(UI_HTML)

@app.route("/api/logs", methods=["GET"])
def get_logs():
	"""
	Get recent log entries from the log file.
	Returns last N lines of logs.
	"""
	try:
		lines = request.args.get('lines', default=100, type=int)
		if lines > 1000:
			lines = 1000  # Limit to 1000 lines max
		
		log_entries = []
		if os.path.exists(LOG_FILE):
			with open(LOG_FILE, 'r', encoding='utf-8') as f:
				all_lines = f.readlines()
				# Get last N lines
				log_entries = all_lines[-lines:]
		
		return jsonify({
			'success': True,
			'logs': log_entries,
			'total_lines': len(log_entries)
		}), 200
	except Exception as e:
		logger.error(f"Error reading logs: {e}", exc_info=True)
		return jsonify({
			'success': False,
			'error': str(e)
		}), 500

@app.route("/api/stats", methods=["GET"])
def get_stats():
	"""
	Get current statistics about the server.
	"""
	try:
		with progress_lock:
			active_uploads = len([p for p in upload_progress.values() if p['status'] in ['uploading', 'converting']])
			total_sessions = len(upload_progress)
		
		# Get log file size
		log_size = 0
		if os.path.exists(LOG_FILE):
			log_size = os.path.getsize(LOG_FILE)
		
		return jsonify({
			'success': True,
			'active_uploads': active_uploads,
			'total_sessions': total_sessions,
			'log_file_size': log_size,
			'save_directory': SAVE_DIR
		}), 200
	except Exception as e:
		logger.error(f"Error getting stats: {e}", exc_info=True)
		return jsonify({
			'success': False,
			'error': str(e)
		}), 500

@app.route("/progress/<session_id>", methods=["GET"])
def get_progress(session_id):
	"""
	Get upload progress for a session.
	"""
	with progress_lock:
		if session_id in upload_progress:
			progress = upload_progress[session_id].copy()
			return jsonify(progress), 200
		return jsonify({"error": "Session not found"}), 404

@app.route("/upload", methods=["POST"])
def upload():
	# Generate session ID for progress tracking
	session_id = str(uuid.uuid4())
	total_size = request.content_length
	
	# Initialize progress tracking
	with progress_lock:
		upload_progress[session_id] = {
			'bytes_received': 0,
			'total_bytes': total_size or 0,
			'status': 'uploading',
			'file_path': None,
			'filename': None
		}
	
	# Debug: Log request details
	logger.debug(f"Content-Type: {request.content_type}")
	logger.debug(f"Content-Length: {request.content_length}")
	logger.debug(f"Session ID: {session_id}")
	
	# Handle multipart/form-data file uploads
	if request.files:
		logger.debug(f"Files in request: {list(request.files.keys())}")
		logger.debug(f"Form data: {list(request.form.keys())}")
		
		if "file" not in request.files:
			logger.error(f"'file' key not found. Available keys: {list(request.files.keys())}")
			return f"No 'file' key found. Available keys: {list(request.files.keys())}", 400

		file = request.files["file"]
		
		# Check if file is actually provided (not empty)
		if file.filename == "":
			logger.error("File filename is empty")
			with progress_lock:
				if session_id in upload_progress:
					del upload_progress[session_id]
			return "No file selected", 400
		
		filename = file.filename
		file_size = 0
		
		# Update progress with filename
		with progress_lock:
			if session_id in upload_progress:
				upload_progress[session_id]['filename'] = filename
		
		# Stream file to temporary location for processing
		temp_file = tempfile.NamedTemporaryFile(delete=False)
		temp_path = temp_file.name
		sha256 = hashlib.sha256()
		
		# Stream file and calculate checksum simultaneously
		file.seek(0)
		while True:
			chunk = file.read(CHUNK_SIZE)
			if not chunk:
				break
			temp_file.write(chunk)
			sha256.update(chunk)
			file_size += len(chunk)
			
			# Update progress
			with progress_lock:
				if session_id in upload_progress:
					upload_progress[session_id]['bytes_received'] = file_size
					if total_size:
						upload_progress[session_id]['total_bytes'] = total_size
		
		temp_file.close()
		checksum = sha256.hexdigest()
		
		# Check for duplicates
		checksum_db = load_checksum_db()
		is_dup, existing_path = is_duplicate(checksum, checksum_db)
		if is_dup:
			logger.info(f"Duplicate file detected (checksum: {checksum[:16]}...). Existing file: {existing_path}")
			os.remove(temp_path)
			with progress_lock:
				if session_id in upload_progress:
					upload_progress[session_id]['status'] = 'duplicate'
					upload_progress[session_id]['file_path'] = existing_path
			return jsonify({"status": "duplicate", "session_id": session_id, "existing_path": existing_path}), 200
		
		# Read a sample for date extraction (first 64KB should be enough for EXIF)
		with open(temp_path, 'rb') as f:
			sample_data = f.read(64 * 1024)
		
		# Try to get date from image if it's an image file
		image_date = datetime.now()
		if file.content_type and file.content_type.startswith("image/"):
			image_date = get_image_date(sample_data)
		else:
			# For non-image files, use current date
			image_date = datetime.now()
		
		# Get year folder path
		year = image_date.year
		year_folder = get_year_folder_path(year)
		
		# Check if file is .quicktime or video/quicktime content type and convert to .mp4
		is_quicktime = (filename.lower().endswith('.quicktime') or 
		               (file.content_type and file.content_type.lower() == 'video/quicktime'))
		if is_quicktime:
			# Move temp file to year folder
			temp_final_path = os.path.join(year_folder, filename)
			os.rename(temp_path, temp_final_path)
			
			# Generate output filename with .mp4 extension
			base_name = os.path.splitext(filename)[0]
			output_filename = f"{base_name}.mp4"
			save_path = os.path.join(year_folder, output_filename)
			
			# Update progress
			with progress_lock:
				if session_id in upload_progress:
					upload_progress[session_id]['status'] = 'converting'
			
			# Convert to mp4 in background thread for better performance
			def convert_in_background():
				if convert_quicktime_to_mp4(temp_final_path, save_path, session_id):
					try:
						os.remove(temp_final_path)
						logger.info(f"Removed original .quicktime file: {temp_final_path}")
					except Exception as e:
						logger.warning(f"Could not remove original file {temp_final_path}: {e}")
					
					# Calculate final checksum and update database
					with open(save_path, 'rb') as f:
						final_file_data = f.read()
					final_checksum = calculate_checksum(final_file_data)
					
					checksum_db = load_checksum_db()
					checksum_db[checksum] = save_path
					checksum_db[final_checksum] = save_path
					save_checksum_db(checksum_db)
					
					with progress_lock:
						if session_id in upload_progress:
							upload_progress[session_id]['status'] = 'completed'
							upload_progress[session_id]['file_path'] = save_path
				else:
					# Conversion failed or ffmpeg not found, keep original
					checksum_db = load_checksum_db()
					checksum_db[checksum] = temp_final_path
					save_checksum_db(checksum_db)
					
					with progress_lock:
						if session_id in upload_progress:
							# Check if it was an ffmpeg not found error
							if upload_progress[session_id].get('status') == 'ffmpeg_not_found':
								upload_progress[session_id]['status'] = 'completed_no_conversion'
								upload_progress[session_id]['warning'] = 'File saved but conversion skipped: ffmpeg not found'
							else:
								upload_progress[session_id]['status'] = 'completed'
							upload_progress[session_id]['file_path'] = temp_final_path
			
			threading.Thread(target=convert_in_background, daemon=True).start()
			save_path = save_path  # Will be updated by background thread
			
		else:
			# Move temp file to final location
			save_path = os.path.join(year_folder, filename)
			os.rename(temp_path, save_path)
			
			# Store checksum in database
			checksum_db[checksum] = save_path
			save_checksum_db(checksum_db)
			
			with progress_lock:
				if session_id in upload_progress:
					upload_progress[session_id]['status'] = 'completed'
					upload_progress[session_id]['file_path'] = save_path
		
		logger.info(f"Successfully received and saved: {save_path} (Year: {year}, Checksum: {checksum[:16]}...)")
		return jsonify({"status": "ok", "session_id": session_id, "file_path": save_path}), 200
	
	# Handle raw binary data (e.g., Content-Type: image/png)
	if request.content_type and request.content_type.startswith("image/"):
		# Stream data to temp file
		temp_file = tempfile.NamedTemporaryFile(delete=False)
		temp_path = temp_file.name
		sha256 = hashlib.sha256()
		file_size = 0
		
		# Stream request data
		for chunk in request.stream:
			if not chunk:
				break
			temp_file.write(chunk)
			sha256.update(chunk)
			file_size += len(chunk)
			
			# Update progress
			with progress_lock:
				if session_id in upload_progress:
					upload_progress[session_id]['bytes_received'] = file_size
		
		temp_file.close()
		checksum = sha256.hexdigest()
		
		if file_size == 0:
			logger.error("No data in request body")
			os.remove(temp_path)
			with progress_lock:
				if session_id in upload_progress:
					del upload_progress[session_id]
			return "No data received", 400
		
		# Check for duplicates
		checksum_db = load_checksum_db()
		is_dup, existing_path = is_duplicate(checksum, checksum_db)
		if is_dup:
			logger.info(f"Duplicate file detected (checksum: {checksum[:16]}...). Existing file: {existing_path}")
			os.remove(temp_path)
			with progress_lock:
				if session_id in upload_progress:
					upload_progress[session_id]['status'] = 'duplicate'
					upload_progress[session_id]['file_path'] = existing_path
			return jsonify({"status": "duplicate", "session_id": session_id, "existing_path": existing_path}), 200
		
		# Read sample for date extraction
		with open(temp_path, 'rb') as f:
			sample_data = f.read(64 * 1024)
		
		# Extract date from image data
		image_date = get_image_date(sample_data)
		year = image_date.year
		
		# Try to get filename from Content-Disposition header
		filename = None
		content_disposition = request.headers.get("Content-Disposition", "")
		if "filename=" in content_disposition:
			filename = content_disposition.split("filename=")[1].strip('"\'')
		
		# If no filename in header, generate one based on content type and timestamp
		if not filename:
			ext = request.content_type.split("/")[1]  # e.g., "png" from "image/png"
			timestamp = image_date.strftime("%Y%m%d_%H%M%S_%f")
			filename = f"image_{timestamp}.{ext}"
		
		# Update progress with filename
		with progress_lock:
			if session_id in upload_progress:
				upload_progress[session_id]['filename'] = filename
		
		# Get year folder path
		year_folder = get_year_folder_path(year)
		save_path = os.path.join(year_folder, filename)
		
		# Move temp file to final location
		os.rename(temp_path, save_path)
		
		# Store checksum in database
		checksum_db[checksum] = save_path
		save_checksum_db(checksum_db)
		
		with progress_lock:
			if session_id in upload_progress:
				upload_progress[session_id]['status'] = 'completed'
				upload_progress[session_id]['file_path'] = save_path
		
		logger.info(f"Successfully received and saved raw image: {save_path} ({file_size} bytes, Year: {year}, Checksum: {checksum[:16]}...)")
		return jsonify({"status": "ok", "session_id": session_id, "file_path": save_path}), 200
	
	# Handle other raw binary data
	# Stream data to temp file
	temp_file = tempfile.NamedTemporaryFile(delete=False)
	temp_path = temp_file.name
	sha256 = hashlib.sha256()
	file_size = 0
	
	# Stream request data
	for chunk in request.stream:
		if not chunk:
			break
		temp_file.write(chunk)
		sha256.update(chunk)
		file_size += len(chunk)
		
		# Update progress
		with progress_lock:
			if session_id in upload_progress:
				upload_progress[session_id]['bytes_received'] = file_size
	
	temp_file.close()
	
	if file_size == 0:
		os.remove(temp_path)
		with progress_lock:
			if session_id in upload_progress:
				del upload_progress[session_id]
		logger.error("No files or data found in request")
		return "No files or data in request", 400
	
	checksum = sha256.hexdigest()
	checksum_db = load_checksum_db()
	
	# Check for duplicates
	is_dup, existing_path = is_duplicate(checksum, checksum_db)
	if is_dup:
		logger.info(f"Duplicate file detected (checksum: {checksum[:16]}...). Existing file: {existing_path}")
		os.remove(temp_path)
		with progress_lock:
			if session_id in upload_progress:
				upload_progress[session_id]['status'] = 'duplicate'
				upload_progress[session_id]['file_path'] = existing_path
		return jsonify({"status": "duplicate", "session_id": session_id, "existing_path": existing_path}), 200
	
	# Use current date for non-image files
	file_date = datetime.now()
	year = file_date.year
	
	# Try to get filename from headers
	filename = None
	content_disposition = request.headers.get("Content-Disposition", "")
	if "filename=" in content_disposition:
		filename = content_disposition.split("filename=")[1].strip('"\'')
	
	if not filename:
		ext = "bin"
		if request.content_type:
			# Try to extract extension from content type
			content_type_lower = request.content_type.lower()
			if content_type_lower == 'video/quicktime':
				ext = "quicktime"
			else:
				parts = request.content_type.split("/")
				if len(parts) > 1:
					ext = parts[1].split(";")[0]  # Remove charset if present
		timestamp = file_date.strftime("%Y%m%d_%H%M%S_%f")
		filename = f"file_{timestamp}.{ext}"
	
	# Update progress with filename
	with progress_lock:
		if session_id in upload_progress:
			upload_progress[session_id]['filename'] = filename
	
	# Get year folder path
	year_folder = get_year_folder_path(year)
	save_path = os.path.join(year_folder, filename)
	
	# Move temp file to final location
	os.rename(temp_path, save_path)
	
	# Check if file is .quicktime or video/quicktime content type and convert to .mp4
	is_quicktime = (filename.lower().endswith('.quicktime') or 
	               (request.content_type and request.content_type.lower() == 'video/quicktime'))
	if is_quicktime:
		# Generate output filename with .mp4 extension
		base_name = os.path.splitext(filename)[0]
		output_filename = f"{base_name}.mp4"
		output_path = os.path.join(year_folder, output_filename)
		
		# Update progress
		with progress_lock:
			if session_id in upload_progress:
				upload_progress[session_id]['status'] = 'converting'
		
		# Convert to mp4 in background thread
		def convert_in_background():
			if convert_quicktime_to_mp4(save_path, output_path, session_id):
				try:
					os.remove(save_path)
					logger.info(f"Removed original .quicktime file: {save_path}")
				except Exception as e:
					logger.warning(f"Could not remove original file {save_path}: {e}")
				
				# Calculate final checksum and update database
				with open(output_path, 'rb') as f:
					final_file_data = f.read()
				final_checksum = calculate_checksum(final_file_data)
				
				checksum_db = load_checksum_db()
				checksum_db[checksum] = output_path
				checksum_db[final_checksum] = output_path
				save_checksum_db(checksum_db)
				
				with progress_lock:
					if session_id in upload_progress:
						upload_progress[session_id]['status'] = 'completed'
						upload_progress[session_id]['file_path'] = output_path
			else:
				# Conversion failed or ffmpeg not found, keep original
				checksum_db = load_checksum_db()
				checksum_db[checksum] = save_path
				save_checksum_db(checksum_db)
				
				with progress_lock:
					if session_id in upload_progress:
						# Check if it was an ffmpeg not found error
						if upload_progress[session_id].get('status') == 'ffmpeg_not_found':
							upload_progress[session_id]['status'] = 'completed_no_conversion'
							upload_progress[session_id]['warning'] = 'File saved but conversion skipped: ffmpeg not found'
						else:
							upload_progress[session_id]['status'] = 'completed'
						upload_progress[session_id]['file_path'] = save_path
		
		threading.Thread(target=convert_in_background, daemon=True).start()
	else:
		# Store checksum in database
		checksum_db[checksum] = save_path
		save_checksum_db(checksum_db)
		
		with progress_lock:
			if session_id in upload_progress:
				upload_progress[session_id]['status'] = 'completed'
				upload_progress[session_id]['file_path'] = save_path
	
	logger.info(f"Successfully received and saved raw data: {save_path} ({file_size} bytes, Year: {year}, Checksum: {checksum[:16]}...)")
	return jsonify({"status": "ok", "session_id": session_id, "file_path": save_path}), 200
	

if __name__ == "__main__":
	logger.info("Starting Flask backup receiver on 0.0.0.0:5001")
	logger.info(f"Save directory: {SAVE_DIR}")
	logger.info(f"Log file: {LOG_FILE}")
	try:
		app.run(host="0.0.0.0", port=5001, debug=False)
	except KeyboardInterrupt:
		logger.info("Shutting down Flask backup receiver...")
	except Exception as e:
		logger.error(f"Error running Flask app: {e}", exc_info=True)
