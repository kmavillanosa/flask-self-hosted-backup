from flask import Flask, request
import os
from datetime import datetime

SAVE_DIR = r"C:\\your\\path\\to\\save\\files"

app = Flask(__name__)
os.makedirs(SAVE_DIR, exist_ok=True)

@app.route("/upload", methods=["POST"])
def upload():
	# Debug: Print request details
	print(f"Content-Type: {request.content_type}")
	print(f"Content-Length: {request.content_length}")
	
	# Handle multipart/form-data file uploads
	if request.files:
		print(f"Files in request: {list(request.files.keys())}")
		print(f"Form data: {list(request.form.keys())}")
		
		if "file" not in request.files:
			print(f"ERROR: 'file' key not found. Available keys: {list(request.files.keys())}")
			return f"No 'file' key found. Available keys: {list(request.files.keys())}", 400

		file = request.files["file"]
		
		# Check if file is actually provided (not empty)
		if file.filename == "":
			print("ERROR: File filename is empty")
			return "No file selected", 400
		
		save_path = os.path.join(SAVE_DIR, file.filename)
		file.save(save_path)
		print(f"Successfully received and saved: {save_path}")
		return "OK", 200
	
	# Handle raw binary data (e.g., Content-Type: image/png)
	if request.content_type and request.content_type.startswith("image/"):
		data = request.get_data()
		
		if not data:
			print("ERROR: No data in request body")
			return "No data received", 400
		
		# Try to get filename from Content-Disposition header
		filename = None
		content_disposition = request.headers.get("Content-Disposition", "")
		if "filename=" in content_disposition:
			filename = content_disposition.split("filename=")[1].strip('"\'')
		
		# If no filename in header, generate one based on content type and timestamp
		if not filename:
			ext = request.content_type.split("/")[1]  # e.g., "png" from "image/png"
			timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
			filename = f"image_{timestamp}.{ext}"
		
		save_path = os.path.join(SAVE_DIR, filename)
		
		with open(save_path, "wb") as f:
			f.write(data)
		
		print(f"Successfully received and saved raw image: {save_path} ({len(data)} bytes)")
		return "OK", 200
	
	# Handle other raw binary data
	data = request.get_data()
	if data:
		# Try to get filename from headers
		filename = None
		content_disposition = request.headers.get("Content-Disposition", "")
		if "filename=" in content_disposition:
			filename = content_disposition.split("filename=")[1].strip('"\'')
		
		if not filename:
			ext = "bin"
			if request.content_type:
				# Try to extract extension from content type
				parts = request.content_type.split("/")
				if len(parts) > 1:
					ext = parts[1].split(";")[0]  # Remove charset if present
			timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
			filename = f"file_{timestamp}.{ext}"
		
		save_path = os.path.join(SAVE_DIR, filename)
		
		with open(save_path, "wb") as f:
			f.write(data)
		
		print(f"Successfully received and saved raw data: {save_path} ({len(data)} bytes)")
		return "OK", 200
	
	print("ERROR: No files or data found in request")
	return "No files or data in request", 400

if __name__ == "__main__":
	app.run(host="0.0.0.0", port=5001)
