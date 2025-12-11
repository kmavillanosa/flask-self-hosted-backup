from flask import Flask, request, jsonify, render_template_string, Response
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
import plistlib

try:
	from flask_cors import CORS
	HAS_CORS = True
except ImportError:
	HAS_CORS = False

# Default save directory (fallback if config doesn't exist)
DEFAULT_SAVE_DIR = r"C:\Users\kmavillanosa\Pictures\IPHONE"
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
CHUNK_SIZE = 8 * 1024 * 1024  # 8MB chunks for ultra-fast streaming
PROGRESS_UPDATE_INTERVAL = 1024 * 1024  # Update progress every 1MB instead of every chunk
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
LOG_FILE = os.path.join(LOG_DIR, 'receiver.log')

# Config file lock for thread-safe access
config_lock = threading.Lock()

def load_config():
	"""
	Load configuration from config.json file.
	Returns a dictionary with configuration values.
	"""
	if os.path.exists(CONFIG_FILE):
		try:
			with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
				config = json.load(f)
				return config
		except Exception as e:
			# Use print since logger may not be initialized yet
			print(f"Warning: Could not load config file: {e}")
			return {}
	return {}

def save_config(config):
	"""
	Save configuration to config.json file.
	"""
	try:
		with config_lock:
			# Use atomic write: write to temp file then rename
			temp_path = CONFIG_FILE + '.tmp'
			with open(temp_path, 'w', encoding='utf-8') as f:
				json.dump(config, f, indent=2)
			os.replace(temp_path, CONFIG_FILE)
		return True
	except Exception as e:
		# Use print since logger may not be initialized yet
		print(f"Error: Could not save config file: {e}")
		return False

def get_save_dir():
	"""
	Get the save directory from config, or return default.
	"""
	config = load_config()
	save_dir = config.get('save_directory', DEFAULT_SAVE_DIR)
	# Ensure directory exists
	os.makedirs(save_dir, exist_ok=True)
	return save_dir

def set_save_dir(new_dir):
	"""
	Set the save directory in config file.
	Validates that the directory exists or can be created.
	"""
	if not new_dir or not new_dir.strip():
		raise ValueError("Save directory cannot be empty")
	
	new_dir = new_dir.strip()
	
	# Try to create directory to validate path
	try:
		os.makedirs(new_dir, exist_ok=True)
	except Exception as e:
		raise ValueError(f"Cannot create directory: {e}")
	
	# Update config
	config = load_config()
	config['save_directory'] = new_dir
	if save_config(config):
		return new_dir
	else:
		raise RuntimeError("Failed to save configuration")

# Initialize SAVE_DIR from config
SAVE_DIR = get_save_dir()
CHECKSUM_DB_PATH = os.path.join(SAVE_DIR, '.checksums.json')

# Create logs directory if it doesn't exist
os.makedirs(LOG_DIR, exist_ok=True)

# Configure logging - Only WARNING and above, except for successful uploads
logging.basicConfig(
	level=logging.WARNING,
	format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
	handlers=[
		logging.FileHandler(LOG_FILE, encoding='utf-8'),
		logging.StreamHandler(sys.stdout)  # Also log to console
	]
)
logger = logging.getLogger(__name__)

# Create a separate logger for successful uploads that always logs
upload_logger = logging.getLogger('uploads')
upload_logger.setLevel(logging.INFO)
# Add handler if not already added
if not upload_logger.handlers:
	upload_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
	upload_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
	upload_logger.addHandler(upload_handler)
	upload_logger.propagate = False

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

# SAVE_DIR is already created in get_save_dir()

# Progress tracking: session_id -> {bytes_received, total_bytes, status, file_path}
upload_progress = {}
progress_lock = threading.Lock()

# In-memory checksum cache for ultra-fast duplicate detection
checksum_cache = {}
checksum_cache_lock = threading.Lock()
checksum_cache_dirty = False

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
			font-family: Arial, sans-serif;
			background: #f5f5f5;
			padding: 20px;
		}
		
		.container {
			max-width: 1200px;
			margin: 0 auto;
			background: white;
			border: 1px solid #ddd;
		}
		
		.header {
			background: #333;
			color: white;
			padding: 20px;
			text-align: center;
		}
		
		.header h1 {
			font-size: 24px;
			margin-bottom: 5px;
		}
		
		.stats-bar {
			background: #f9f9f9;
			padding: 15px;
			display: flex;
			justify-content: space-around;
			flex-wrap: wrap;
			border-bottom: 1px solid #ddd;
		}
		
		.stat-item {
			text-align: center;
			padding: 10px;
		}
		
		.stat-label {
			font-size: 12px;
			color: #666;
			margin-bottom: 5px;
		}
		
		.stat-value {
			font-size: 20px;
			font-weight: bold;
			color: #333;
		}
		
		.controls {
			padding: 15px;
			background: #f9f9f9;
			display: flex;
			justify-content: space-between;
			align-items: center;
			flex-wrap: wrap;
			gap: 10px;
			border-bottom: 1px solid #ddd;
		}
		
		.settings-section {
			padding: 15px;
			background: #f9f9f9;
			border-bottom: 1px solid #ddd;
		}
		
		.settings-section h3 {
			font-size: 16px;
			margin-bottom: 10px;
			color: #333;
		}
		
		.directory-input-group {
			display: flex;
			gap: 10px;
			align-items: center;
			flex-wrap: wrap;
		}
		
		.directory-input {
			flex: 1;
			min-width: 300px;
			padding: 8px 12px;
			border: 1px solid #ccc;
			font-size: 14px;
			font-family: monospace;
		}
		
		.directory-display {
			padding: 8px 12px;
			background: white;
			border: 1px solid #ddd;
			font-size: 13px;
			font-family: monospace;
			color: #333;
			word-break: break-all;
			margin-top: 5px;
		}
		
		.btn-save {
			padding: 8px 16px;
			border: none;
			background: #28a745;
			color: white;
			cursor: pointer;
			font-size: 14px;
			border-radius: 4px;
		}
		
		.btn-save:hover {
			background: #218838;
		}
		
		.btn-save:disabled {
			background: #6c757d;
			cursor: not-allowed;
		}
		
		.message {
			margin-top: 10px;
			padding: 8px 12px;
			border-radius: 4px;
			font-size: 13px;
		}
		
		.message.success {
			background: #d4edda;
			border: 1px solid #c3e6cb;
			color: #155724;
		}
		
		.message.error {
			background: #f8d7da;
			border: 1px solid #f5c6cb;
			color: #721c24;
		}
		
		.btn {
			padding: 8px 16px;
			border: 1px solid #ccc;
			background: white;
			cursor: pointer;
			font-size: 14px;
		}
		
		.btn:hover {
			background: #f0f0f0;
		}
		
		.status-indicator {
			display: inline-block;
			width: 10px;
			height: 10px;
			border-radius: 50%;
			margin-right: 5px;
		}
		
		.status-active {
			background: #28a745;
		}
		
		.status-paused {
			background: #ffc107;
		}
		
		.logs-container {
			padding: 15px;
			max-height: 500px;
			overflow-y: auto;
			background: #1e1e1e;
		}
		
		.logs {
			font-family: 'Courier New', monospace;
			font-size: 12px;
			line-height: 1.5;
		}
		
		.log-entry {
			padding: 5px 10px;
			margin-bottom: 2px;
			border-left: 2px solid transparent;
			word-wrap: break-word;
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
		}
		
		.loading {
			text-align: center;
			color: #858585;
			padding: 20px;
		}
	</style>
</head>
<body>
	<div class="container">
		<div class="header">
			<h1>Flask Backup Receiver</h1>
			<a href="/shortcut" style="display: inline-block; margin-top: 15px; padding: 10px 20px; background: #007AFF; color: white; text-decoration: none; border-radius: 5px; font-size: 14px;">📱 Installation Instructions</a>
		</div>
		
		<div class="stats-bar">
			<div class="stat-item">
				<div class="stat-label">Files Saved</div>
				<div class="stat-value" id="file-count">0</div>
			</div>
			<div class="stat-item">
				<div class="stat-label">Total Size</div>
				<div class="stat-value" id="total-size">0 B</div>
			</div>
			<div class="stat-item">
				<div class="stat-label">Active Uploads</div>
				<div class="stat-value" id="active-uploads">0</div>
			</div>
			<div class="stat-item">
				<div class="stat-label">Total Sessions</div>
				<div class="stat-value" id="total-sessions">0</div>
			</div>
		</div>
		
		<div class="settings-section">
			<h3>📁 Save Directory Settings</h3>
			<div class="directory-input-group">
				<input type="text" id="save-directory-input" class="directory-input" placeholder="Enter directory path (e.g., C:\\Users\\YourName\\Pictures\\IPHONE)" />
				<button class="btn-save" onclick="updateSaveDirectory()">Save Directory</button>
			</div>
			<div class="directory-display" id="current-directory">Loading...</div>
			<div id="directory-message"></div>
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
					Filter:
					<select id="filter-select" onchange="updateFilter()">
						<option value="all" selected>All</option>
						<option value="info">INFO</option>
						<option value="debug">DEBUG</option>
						<option value="warning">WARNING</option>
						<option value="error">ERROR</option>
					</select>
				</label>
				<label style="margin-left: 15px;">
					Lines:
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
		let currentFilter = 'all';
		let lastLogCount = 0;
		let lastLogHash = '';
		let allLogs = [];
		
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
			// Scroll the logs container (parent) to bottom
			const logsContainer = document.querySelector('.logs-container');
			if (logsContainer) {
				setTimeout(() => {
					logsContainer.scrollTop = logsContainer.scrollHeight;
				}, 50);
			}
			// Also scroll the container itself as fallback
			setTimeout(() => {
				container.scrollTop = container.scrollHeight;
			}, 50);
		}
		
		function renderLogs(logs) {
			const container = document.getElementById('logs');
			
			if (!logs || logs.length === 0) {
				container.innerHTML = '<div class="empty-logs">No logs available</div>';
				lastLogCount = 0;
				lastLogHash = '';
				allLogs = [];
				return;
			}
			
			// Store all logs for filtering
			allLogs = logs;
			
			// Filter logs based on current filter
			let filteredLogs = logs;
			if (currentFilter !== 'all') {
				filteredLogs = logs.filter(log => {
					const entry = parseLogEntry(log.trim());
					return entry.level === currentFilter;
				});
			}
			
			// Create a hash of the last log entry to detect new logs
			const currentLogHash = logs.length > 0 ? logs[logs.length - 1].trim() : '';
			const hasNewLogs = logs.length > lastLogCount || (logs.length > 0 && currentLogHash !== lastLogHash);
			
			// Always update if there are new logs or count changed
			if (hasNewLogs || logs.length !== lastLogCount) {
				if (filteredLogs.length === 0) {
					container.innerHTML = '<div class="empty-logs">No logs match the selected filter</div>';
				} else {
					container.innerHTML = filteredLogs.map(log => {
						const entry = parseLogEntry(log.trim());
						return `<div class="log-entry ${entry.level}">
							<span class="log-time">${entry.time || ''}</span>
							<span class="log-level">${entry.level}</span>
							<span class="log-message">${escapeHtml(entry.message)}</span>
						</div>`;
					}).join('');
				}
				
				lastLogCount = logs.length;
				lastLogHash = currentLogHash;
				
				// Always scroll to bottom when new logs are detected
				if (hasNewLogs) {
					scrollToBottom(container);
				}
			}
		}
		
		function updateFilter() {
			currentFilter = document.getElementById('filter-select').value;
			// Re-render with current filter
			if (allLogs.length > 0) {
				renderLogs(allLogs);
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
					document.getElementById('file-count').textContent = data.file_count.toLocaleString();
					document.getElementById('total-size').textContent = formatBytes(data.total_files_size || 0);
					document.getElementById('active-uploads').textContent = data.active_uploads;
					document.getElementById('total-sessions').textContent = data.total_sessions;
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
		
		async function fetchSaveDirectory() {
			try {
				const response = await fetch('/api/save-directory');
				const data = await response.json();
				
				if (data.success) {
					document.getElementById('current-directory').textContent = 'Current: ' + data.save_directory;
					document.getElementById('save-directory-input').value = data.save_directory;
				} else {
					document.getElementById('current-directory').textContent = 'Error loading directory';
				}
			} catch (error) {
				console.error('Error fetching save directory:', error);
				document.getElementById('current-directory').textContent = 'Error loading directory';
			}
		}
		
		async function updateSaveDirectory() {
			const input = document.getElementById('save-directory-input');
			const newDir = input.value.trim();
			const messageDiv = document.getElementById('directory-message');
			const saveBtn = document.querySelector('.btn-save');
			
			if (!newDir) {
				messageDiv.innerHTML = '<div class="message error">Please enter a directory path</div>';
				return;
			}
			
			saveBtn.disabled = true;
			saveBtn.textContent = 'Saving...';
			messageDiv.innerHTML = '';
			
			try {
				const response = await fetch('/api/save-directory', {
					method: 'POST',
					headers: {
						'Content-Type': 'application/json'
					},
					body: JSON.stringify({ save_directory: newDir })
				});
				
				const data = await response.json();
				
				if (data.success) {
					messageDiv.innerHTML = '<div class="message success">Directory updated successfully! The server will use this directory for new uploads.</div>';
					document.getElementById('current-directory').textContent = 'Current: ' + data.save_directory;
					// Refresh stats to show updated directory info
					fetchStats();
				} else {
					messageDiv.innerHTML = '<div class="message error">Error: ' + (data.error || 'Failed to update directory') + '</div>';
				}
			} catch (error) {
				messageDiv.innerHTML = '<div class="message error">Error: ' + error.message + '</div>';
			} finally {
				saveBtn.disabled = false;
				saveBtn.textContent = 'Save Directory';
			}
		}
		
		// Allow Enter key to save directory
		document.addEventListener('DOMContentLoaded', function() {
			const input = document.getElementById('save-directory-input');
			if (input) {
				input.addEventListener('keypress', function(e) {
					if (e.key === 'Enter') {
						updateSaveDirectory();
					}
				});
			}
		});
		
		// Initialize
		refreshLogs();
		fetchSaveDirectory();
		startAutoRefresh();
		
		// Refresh stats every 5 seconds
		setInterval(fetchStats, 5000);
	</script>
</body>
</html>'''

def load_checksum_db():
	"""
	Load the checksum database from disk (with caching).
	Returns a dictionary mapping checksum -> file path.
	"""
	global checksum_cache
	
	with checksum_cache_lock:
		if checksum_cache:
			return checksum_cache.copy()
	
	if os.path.exists(CHECKSUM_DB_PATH):
		try:
			with open(CHECKSUM_DB_PATH, 'r', encoding='utf-8') as f:
				db = json.load(f)
				with checksum_cache_lock:
					checksum_cache = db
				return db
		except Exception as e:
			logger.warning(f"Could not load checksum database: {e}")
			return {}
	return {}

def save_checksum_db(checksum_db, immediate=False):
	"""
	Save the checksum database to disk (with caching and async writes).
	"""
	global checksum_cache, checksum_cache_dirty
	
	with checksum_cache_lock:
		checksum_cache = checksum_db.copy()
		checksum_cache_dirty = True
	
	# Save immediately if requested, otherwise save in background
	if immediate:
		_save_checksum_db_sync(checksum_db)
	else:
		# Save in background thread to avoid blocking
		threading.Thread(target=_save_checksum_db_sync, args=(checksum_db,), daemon=True).start()

def _save_checksum_db_sync(checksum_db):
	"""Synchronous save operation."""
	try:
		# Use atomic write: write to temp file then rename
		temp_path = CHECKSUM_DB_PATH + '.tmp'
		with open(temp_path, 'w', encoding='utf-8') as f:
			json.dump(checksum_db, f, separators=(',', ':'))  # Compact JSON for speed
		os.replace(temp_path, CHECKSUM_DB_PATH)
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
			save_checksum_db(checksum_db, immediate=False)
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
	# Always get current save directory (in case it changed)
	current_save_dir = get_save_dir()
	year_path = os.path.join(current_save_dir, str(year))
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
		# Log as warning since videos are still saved without conversion
		logger.warning("ffmpeg not found. Videos will be saved without conversion. Install ffmpeg to enable .quicktime to .mp4 conversion.")
		if session_id:
			with progress_lock:
				if session_id in upload_progress:
					upload_progress[session_id]['status'] = 'ffmpeg_not_found'
					upload_progress[session_id]['warning'] = 'ffmpeg not found. Video saved without conversion.'
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
			# Conversion success - don't log to reduce spam
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
		logger.warning("ffmpeg not found. Videos will be saved without conversion.")
		return False
	except Exception as e:
		logger.error(f"Error converting quicktime to mp4: {e}", exc_info=True)
		return False

@app.route("/download-shortcut", methods=["GET"])
def download_shortcut():
	"""
	Generate and serve a downloadable iOS Shortcuts shortcut file.
	When downloaded on iPhone, it will automatically open in Shortcuts app.
	"""
	# Get server IP and port from request
	host = request.host
	upload_url = f"http://{host}/upload"
	
	# Generate UUIDs for the shortcut
	shortcut_uuid = str(uuid.uuid4()).upper()
	action_uuid1 = str(uuid.uuid4()).upper()
	action_uuid2 = str(uuid.uuid4()).upper()
	output_uuid = str(uuid.uuid4()).upper()
	
	# Create iOS Shortcuts plist structure using plistlib
	shortcut_data = {
		'WFWorkflowActions': [
			{
				'WFWorkflowActionIdentifier': 'is.workflow.actions.selectphotos',
				'WFWorkflowActionParameters': {
					'SelectMultiple': True,
					'SelectPhotos': 0
				},
				'WFWorkflowActionUUID': action_uuid1
			},
			{
				'WFWorkflowActionIdentifier': 'is.workflow.actions.getcontentsofurl',
				'WFWorkflowActionParameters': {
					'WFHTTPMethod': 'POST',
					'WFURL': upload_url,
					'WFHTTPBodyType': 'File',
					'WFHTTPBody': {
						'Value': {
							'attachmentsByRange': {
								'{0, 1}': {
									'OutputName': 'Photos',
									'OutputUUID': output_uuid,
									'Type': 'ActionOutput'
								}
							}
						},
						'WFSerializationType': 'WFTextTokenAttachment'
					}
				},
				'WFWorkflowActionUUID': action_uuid2
			}
		],
		'WFWorkflowClientRelease': '2.0',
		'WFWorkflowClientVersion': '900',
		'WFWorkflowIcon': {
			'WFWorkflowIconGlyphNumber': 59511,
			'WFWorkflowIconStartColor': 4282601983
		},
		'WFWorkflowInputContentItemClasses': [
			'WFPhotoMediaContentItem',
			'WFGenericFileContentItem'
		],
		'WFWorkflowMinimumClientVersion': 900,
		'WFWorkflowMinimumClientRelease': '2.0',
		'WFWorkflowTypes': [
			'NCWidget',
			'WatchKit'
		]
	}
	
	# Create binary plist
	plist_buffer = BytesIO()
	plistlib.dump(shortcut_data, plist_buffer, fmt=plistlib.FMT_BINARY)
	plist_buffer.seek(0)
	
	# Return as downloadable file
	response = Response(
		plist_buffer.getvalue(),
		mimetype='application/x-plist',
		headers={
			'Content-Disposition': 'attachment; filename="Backup-to-Server.shortcut"'
		}
	)
	return response

@app.route("/static/<filename>", methods=["GET"])
def serve_static(filename):
	"""
	Serve static files like images.
	"""
	try:
		file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
		if os.path.exists(file_path) and os.path.isfile(file_path):
			# Determine content type
			if filename.lower().endswith('.jpg') or filename.lower().endswith('.jpeg'):
				mimetype = 'image/jpeg'
			elif filename.lower().endswith('.png'):
				mimetype = 'image/png'
			elif filename.lower().endswith('.gif'):
				mimetype = 'image/gif'
			else:
				mimetype = 'application/octet-stream'
			
			with open(file_path, 'rb') as f:
				return Response(f.read(), mimetype=mimetype)
		else:
			return jsonify({"error": "File not found"}), 404
	except Exception as e:
		logger.error(f"Error serving static file: {e}", exc_info=True)
		return jsonify({"error": str(e)}), 500

@app.route("/shortcut", methods=["GET"])
def get_shortcut():
	"""
	Generate and serve an iOS Shortcuts shortcut installation page.
	Provides instructions and shortcut URL for automatic server connection.
	"""
	# Get server IP and port from request
	host = request.host
	upload_url = f"http://{host}/upload"
	
	# Create a shortcut installation page with instructions
	shortcut_html = f'''<!DOCTYPE html>
<html>
<head>
	<meta charset="UTF-8">
	<meta name="viewport" content="width=device-width, initial-scale=1.0">
	<title>Install Backup Shortcut</title>
	<style>
		body {{
			font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
			padding: 15px;
			background: #f5f5f5;
			margin: 0;
		}}
		.container {{
			background: white;
			padding: 20px;
			border-radius: 8px;
			max-width: 550px;
			margin: 0 auto;
			box-shadow: 0 2px 8px rgba(0,0,0,0.1);
		}}
		h1 {{
			color: #333;
			margin-bottom: 8px;
			font-size: 22px;
			font-weight: 600;
		}}
		.container > p {{
			color: #666;
			margin-bottom: 20px;
			font-size: 14px;
			line-height: 1.5;
		}}
		.step {{
			margin: 15px 0;
			padding: 12px;
			background: #f8f9fa;
			border-left: 3px solid #007AFF;
			border-radius: 4px;
		}}
		.step h3 {{
			color: #007AFF;
			margin-top: 0;
			margin-bottom: 8px;
			font-size: 16px;
			font-weight: 600;
		}}
		.step p {{
			color: #555;
			margin: 6px 0;
			line-height: 1.4;
			font-size: 13px;
		}}
		.step ul {{
			margin: 8px 0;
			padding-left: 18px;
			color: #555;
			font-size: 13px;
		}}
		.step ul li {{
			margin: 4px 0;
			line-height: 1.4;
		}}
		.step a {{
			color: #007AFF;
			text-decoration: none;
			font-weight: 500;
		}}
		.step a:hover {{
			text-decoration: underline;
		}}
		.step img {{
			max-width: 200px;
			width: 100%;
			height: auto;
			margin: 10px auto;
			display: block;
			border-radius: 6px;
			box-shadow: 0 2px 6px rgba(0,0,0,0.1);
			border: 1px solid #e0e0e0;
		}}
		.step a img {{
			cursor: pointer;
			transition: opacity 0.2s;
		}}
		.step a:hover img {{
			opacity: 0.8;
		}}
		.code {{
			background: #1e1e1e;
			color: #d4d4d4;
			padding: 8px 12px;
			border-radius: 4px;
			font-family: 'Courier New', monospace;
			font-size: 11px;
			word-break: break-all;
			margin: 8px 0;
			border: 1px solid #333;
		}}
		.btn {{
			display: inline-block;
			padding: 8px 16px;
			background: #007AFF;
			color: white;
			text-decoration: none;
			border-radius: 6px;
			margin: 8px 4px;
			font-size: 13px;
			font-weight: 500;
		}}
		.btn:hover {{
			background: #0056CC;
		}}
	</style>
</head>
<body>
	<div class="container">
		<h1>📱 Install Backup Shortcut</h1>
		<p>Create a Shortcuts workflow to automatically backup photos/videos to this server.</p>
		
		<div class="step">
			<h3>Step 1: Scan QR Code</h3>
			<p><strong>Requirements:</strong></p>
			<ul>
				<li>iPhone and PC should be on the same network</li>
				<li>iPhone Shortcuts app should be installed on phone (<a href="https://apps.apple.com/app/shortcuts/id915249334" target="_blank">Download from App Store</a>)</li>
			</ul>
			<p>Scan the QR code below with your iPhone camera to open the shortcut setup.</p>
			<a href="shortcuts://shortcuts/f067a6f17204474ab3e8029c94e4356a" style="display: block; text-align: center;">
				<img src="/static/qrcode.png" alt="QR Code" style="cursor: pointer;" />
			</a>
			<p style="font-size: 12px; color: #666; margin-top: 8px;">
				Or <a href="https://www.icloud.com/shortcuts/f067a6f17204474ab3e8029c94e4356a" target="_blank">open in browser</a>
			</p>
		</div>
		
		<div class="step">
			<h3>Step 2: Open Shortcuts App</h3>
			<p>Open the Shortcuts app on your iPhone.</p>
			<img src="/static/step1.jpg" alt="Step 2: Open Shortcuts App" />
		</div>
		
		<div class="step">
			<h3>Step 3: Add Actions</h3>
			<p>Copy the setup shown in the screenshot below. Replace the URL in the screenshot with:</p>
			<div class="code">{upload_url}</div>
			<img src="/static/step2.jpg" alt="Step 3: Add Actions" />
		</div>
		
		<div class="step">
			<h3>Step 4: Save Shortcut</h3>
			<p>Name it "Backup to Server" and save.</p>
		</div>
		
		<p style="margin-top: 20px; font-size: 12px; color: #666;">
			Server URL: <code>{upload_url}</code>
		</p>
	</div>
</body>
</html>'''
	
	return render_template_string(shortcut_html)

@app.route("/connect", methods=["GET"])
@app.route("/q", methods=["GET"])
def mobile_connect():
	"""
	Mobile-friendly connection page for server connection.
	"""
	# Get server IP and port from request
	host = request.host
	upload_url = f"http://{host}/upload"
	shortcut_url = f"http://{host}/shortcut"
	
	mobile_html = f'''<!DOCTYPE html>
<html lang="en">
<head>
	<meta charset="UTF-8">
	<meta name="viewport" content="width=device-width, initial-scale=1.0">
	<title>Connect to Backup Server</title>
	<style>
		* {{
			margin: 0;
			padding: 0;
			box-sizing: border-box;
		}}
		body {{
			font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
			background: #f5f5f5;
			padding: 20px;
			display: flex;
			justify-content: center;
			align-items: center;
			min-height: 100vh;
		}}
		.container {{
			background: white;
			border: 1px solid #ddd;
			padding: 30px;
			max-width: 400px;
			width: 100%;
			text-align: center;
		}}
		.icon {{
			font-size: 64px;
			margin-bottom: 20px;
		}}
		h1 {{
			font-size: 24px;
			margin-bottom: 10px;
			color: #333;
		}}
		.info {{
			color: #666;
			font-size: 14px;
			margin-bottom: 20px;
			line-height: 1.5;
		}}
		.url-box {{
			background: #f9f9f9;
			border: 1px solid #ddd;
			padding: 15px;
			margin: 20px 0;
			word-break: break-all;
			font-family: monospace;
			font-size: 12px;
			color: #333;
		}}
		.btn {{
			display: inline-block;
			padding: 12px 24px;
			background: #007AFF;
			color: white;
			text-decoration: none;
			border-radius: 6px;
			margin: 10px 5px;
			font-size: 16px;
		}}
		.btn:hover {{
			background: #0056CC;
		}}
		.btn-secondary {{
			background: #6c757d;
		}}
		.btn-secondary:hover {{
			background: #5a6268;
		}}
		.status {{
			margin-top: 20px;
			padding: 10px;
			background: #d4edda;
			border: 1px solid #c3e6cb;
			color: #155724;
			border-radius: 4px;
			font-size: 14px;
		}}
	</style>
</head>
<body>
	<div class="container">
		<div class="icon">📱</div>
		<h1>Backup Server Connected</h1>
		<p class="info">Your device is connected to the backup server. You can now upload your files.</p>
		
		<div class="url-box">
			{upload_url}
		</div>
		
		<div>
			<a href="/" class="btn">View Dashboard</a>
			<a href="{shortcut_url}" class="btn btn-secondary">Install Shortcut</a>
		</div>
		
		<div style="margin-top: 20px; padding-top: 20px; border-top: 1px solid #ddd;">
			<p style="font-size: 12px; color: #666; margin-bottom: 10px;">Upload endpoint:</p>
			<div class="url-box" style="font-size: 11px;">
				POST {upload_url}
			</div>
		</div>
		
		<div class="status">
			✓ Server is running and ready to receive files
		</div>
	</div>
</body>
</html>'''
	return render_template_string(mobile_html)

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

def count_files_in_directory(directory):
	"""
	Count total number of files in directory (recursively).
	Excludes hidden files and directories.
	"""
	try:
		count = 0
		for root, dirs, files in os.walk(directory):
			# Skip hidden directories
			dirs[:] = [d for d in dirs if not d.startswith('.')]
			# Count files (excluding hidden files)
			count += len([f for f in files if not f.startswith('.')])
		return count
	except Exception as e:
		logger.warning(f"Error counting files: {e}")
		return 0

def get_total_files_size(directory):
	"""
	Calculate total size of all files in directory (recursively).
	Excludes hidden files and directories.
	Returns size in bytes.
	"""
	try:
		total_size = 0
		for root, dirs, files in os.walk(directory):
			# Skip hidden directories
			dirs[:] = [d for d in dirs if not d.startswith('.')]
			# Calculate size of files (excluding hidden files)
			for file in files:
				if not file.startswith('.'):
					file_path = os.path.join(root, file)
					try:
						total_size += os.path.getsize(file_path)
					except (OSError, FileNotFoundError):
						# Skip files that can't be accessed
						continue
		return total_size
	except Exception as e:
		logger.warning(f"Error calculating total file size: {e}")
		return 0

@app.route("/api/save-directory", methods=["GET"])
def get_save_directory():
	"""
	Get the current save directory setting.
	"""
	try:
		current_dir = get_save_dir()
		return jsonify({
			'success': True,
			'save_directory': current_dir
		}), 200
	except Exception as e:
		logger.error(f"Error getting save directory: {e}", exc_info=True)
		return jsonify({
			'success': False,
			'error': str(e)
		}), 500

@app.route("/api/save-directory", methods=["POST"])
def update_save_directory():
	"""
	Update the save directory setting.
	"""
	try:
		data = request.get_json()
		if not data or 'save_directory' not in data:
			return jsonify({
				'success': False,
				'error': 'save_directory field is required'
			}), 400
		
		new_dir = data['save_directory']
		
		# Update the save directory
		updated_dir = set_save_dir(new_dir)
		
		# Update global SAVE_DIR and CHECKSUM_DB_PATH
		global SAVE_DIR, CHECKSUM_DB_PATH
		SAVE_DIR = updated_dir
		CHECKSUM_DB_PATH = os.path.join(SAVE_DIR, '.checksums.json')
		
		logger.info(f"Save directory updated to: {updated_dir}")
		
		return jsonify({
			'success': True,
			'save_directory': updated_dir,
			'message': 'Save directory updated successfully'
		}), 200
	except ValueError as e:
		return jsonify({
			'success': False,
			'error': str(e)
		}), 400
	except Exception as e:
		logger.error(f"Error updating save directory: {e}", exc_info=True)
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
		
		# Get current save directory (may have changed)
		current_save_dir = get_save_dir()
		
		# Count files in save directory
		file_count = count_files_in_directory(current_save_dir)
		
		# Calculate total size of all uploaded files
		total_files_size = get_total_files_size(current_save_dir)
		
		return jsonify({
			'success': True,
			'active_uploads': active_uploads,
			'total_sessions': total_sessions,
			'log_file_size': log_size,
			'file_count': file_count,
			'total_files_size': total_files_size,
			'save_directory': current_save_dir
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
	# Handle multipart/form-data file uploads
	if request.files:
		
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
		
		# Stream file and calculate checksum simultaneously with optimized I/O
		file.seek(0)
		last_progress_update = 0
		while True:
			chunk = file.read(CHUNK_SIZE)
			if not chunk:
				break
			temp_file.write(chunk)
			sha256.update(chunk)
			file_size += len(chunk)
			
			# Update progress less frequently for better performance
			if file_size - last_progress_update >= PROGRESS_UPDATE_INTERVAL:
				with progress_lock:
					if session_id in upload_progress:
						upload_progress[session_id]['bytes_received'] = file_size
						if total_size:
							upload_progress[session_id]['total_bytes'] = total_size
				last_progress_update = file_size
		
		temp_file.close()
		checksum = sha256.hexdigest()
		
		# Check for duplicates (using cached DB for speed)
		checksum_db = load_checksum_db()
		is_dup, existing_path = is_duplicate(checksum, checksum_db)
		if is_dup:
			# Duplicate detected - don't log to reduce spam
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
		elif file.content_type and file.content_type.startswith("video/"):
			# For video files, use current date (could be enhanced to extract video metadata)
			image_date = datetime.now()
		else:
			# For other files, use current date
			image_date = datetime.now()
		
		# Get year folder path
		year = image_date.year
		year_folder = get_year_folder_path(year)
		
		# Check if file is a video file (various formats)
		filename_lower = filename.lower()
		is_video = (
			filename_lower.endswith('.mov') or
			filename_lower.endswith('.mp4') or
			filename_lower.endswith('.m4v') or
			filename_lower.endswith('.quicktime') or
			(file.content_type and file.content_type.lower().startswith('video/'))
		)
		
		# Only convert .quicktime files to .mp4, save other videos as-is
		is_quicktime = (filename_lower.endswith('.quicktime') or 
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
						# File removal - don't log to reduce spam
					except Exception as e:
						logger.warning(f"Could not remove original file {temp_final_path}: {e}")
					
					# Calculate final checksum and update database
					with open(save_path, 'rb') as f:
						final_file_data = f.read()
					final_checksum = calculate_checksum(final_file_data)
					
					checksum_db = load_checksum_db()
					checksum_db[checksum] = save_path
					checksum_db[final_checksum] = save_path
					save_checksum_db(checksum_db, immediate=False)
					
					with progress_lock:
						if session_id in upload_progress:
							upload_progress[session_id]['status'] = 'completed'
							upload_progress[session_id]['file_path'] = save_path
				else:
					# Conversion failed or ffmpeg not found, keep original
					checksum_db = load_checksum_db()
					checksum_db[checksum] = temp_final_path
					save_checksum_db(checksum_db, immediate=False)
					
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
			save_checksum_db(checksum_db, immediate=False)
			
			with progress_lock:
				if session_id in upload_progress:
					upload_progress[session_id]['status'] = 'completed'
					upload_progress[session_id]['file_path'] = save_path
			
			# Log successful upload (including videos)
			file_type = "video" if is_video else "file"
			upload_logger.info(f"Successfully received and saved {file_type}: {save_path} (Year: {year}, Checksum: {checksum[:16]}...)")
			return jsonify({"status": "ok", "session_id": session_id, "file_path": save_path}), 200
	
	# Handle raw binary data (e.g., Content-Type: image/png)
	if request.content_type and request.content_type.startswith("image/"):
		# Stream data to temp file
		temp_file = tempfile.NamedTemporaryFile(delete=False)
		temp_path = temp_file.name
		sha256 = hashlib.sha256()
		file_size = 0
		
		# Stream request data with optimized progress updates
		last_progress_update = 0
		for chunk in request.stream:
			if not chunk:
				break
			temp_file.write(chunk)
			sha256.update(chunk)
			file_size += len(chunk)
			
			# Update progress less frequently for better performance
			if file_size - last_progress_update >= PROGRESS_UPDATE_INTERVAL:
				with progress_lock:
					if session_id in upload_progress:
						upload_progress[session_id]['bytes_received'] = file_size
				last_progress_update = file_size
		
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
			# Duplicate detected - don't log to reduce spam
			os.remove(temp_path)
			with progress_lock:
				if session_id in upload_progress:
					upload_progress[session_id]['status'] = 'duplicate'
					upload_progress[session_id]['file_path'] = existing_path
			return jsonify({"status": "duplicate", "session_id": session_id, "existing_path": existing_path}), 200
		
		# Read sample for date extraction (optimized buffer size)
		with open(temp_path, 'rb', buffering=32768) as f:
			sample_data = f.read(32 * 1024)
		
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
		
		# Store checksum in database (async save for speed)
		checksum_db[checksum] = save_path
		save_checksum_db(checksum_db, immediate=False)
		
		with progress_lock:
			if session_id in upload_progress:
				upload_progress[session_id]['status'] = 'completed'
				upload_progress[session_id]['file_path'] = save_path
		
		# Log successful upload
		upload_logger.info(f"Successfully received and saved raw image: {save_path} ({file_size} bytes, Year: {year}, Checksum: {checksum[:16]}...)")
		return jsonify({"status": "ok", "session_id": session_id, "file_path": save_path}), 200
	
	# Handle other raw binary data
	# Stream data to temp file
	temp_file = tempfile.NamedTemporaryFile(delete=False)
	temp_path = temp_file.name
	sha256 = hashlib.sha256()
	file_size = 0
	
	# Stream request data with optimized progress updates
	last_progress_update = 0
	for chunk in request.stream:
		if not chunk:
			break
		temp_file.write(chunk)
		sha256.update(chunk)
		file_size += len(chunk)
		
		# Update progress less frequently for better performance
		if file_size - last_progress_update >= PROGRESS_UPDATE_INTERVAL:
			with progress_lock:
				if session_id in upload_progress:
					upload_progress[session_id]['bytes_received'] = file_size
			last_progress_update = file_size
	
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
		# Duplicate detected - don't log to reduce spam
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
			if content_type_lower.startswith('video/'):
				# Map common video content types to extensions
				video_ext_map = {
					'video/quicktime': 'mov',
					'video/mp4': 'mp4',
					'video/x-m4v': 'm4v',
					'video/mpeg': 'mpg',
					'video/avi': 'avi'
				}
				ext = video_ext_map.get(content_type_lower, 'mov')  # Default to .mov for videos
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
	
	# Check if file is a video file (various formats)
	filename_lower = filename.lower() if filename else ''
	is_video = (
		filename_lower.endswith('.mov') or
		filename_lower.endswith('.mp4') or
		filename_lower.endswith('.m4v') or
		filename_lower.endswith('.quicktime') or
		(request.content_type and request.content_type.lower().startswith('video/'))
	)
	
	# Only convert .quicktime files to .mp4, save other videos as-is
	is_quicktime = (filename_lower.endswith('.quicktime') or 
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
					# File removal - don't log to reduce spam
				except Exception as e:
					logger.warning(f"Could not remove original file {save_path}: {e}")
				
				# Calculate final checksum and update database
				with open(output_path, 'rb') as f:
					final_file_data = f.read()
				final_checksum = calculate_checksum(final_file_data)
				
				checksum_db = load_checksum_db()
				checksum_db[checksum] = output_path
				checksum_db[final_checksum] = output_path
				save_checksum_db(checksum_db, immediate=False)
				
				with progress_lock:
					if session_id in upload_progress:
						upload_progress[session_id]['status'] = 'completed'
						upload_progress[session_id]['file_path'] = output_path
			else:
				# Conversion failed or ffmpeg not found, keep original
				checksum_db = load_checksum_db()
				checksum_db[checksum] = save_path
				save_checksum_db(checksum_db, immediate=False)
				
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
		# Store checksum in database (async save for speed)
		checksum_db[checksum] = save_path
		save_checksum_db(checksum_db, immediate=False)
		
		with progress_lock:
			if session_id in upload_progress:
				upload_progress[session_id]['status'] = 'completed'
				upload_progress[session_id]['file_path'] = save_path
	
	# Log successful upload
	upload_logger.info(f"Successfully received and saved raw data: {save_path} ({file_size} bytes, Year: {year}, Checksum: {checksum[:16]}...)")
	return jsonify({"status": "ok", "session_id": session_id, "file_path": save_path}), 200
	

if __name__ == "__main__":
	# Only log startup once, use print for visibility
	print(f"Starting Flask backup receiver on 0.0.0.0:5001")
	print(f"Save directory: {SAVE_DIR}")
	print(f"Log file: {LOG_FILE}")
	try:
		app.run(host="0.0.0.0", port=5001, debug=False)
	except KeyboardInterrupt:
		print("Shutting down Flask backup receiver...")
	except Exception as e:
		logger.error(f"Error running Flask app: {e}", exc_info=True)
