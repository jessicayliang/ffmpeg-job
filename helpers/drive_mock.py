def extract_file_id(drive_url): return "fake-id"
def download_video(file_id, dest_path):
    # Copy a local video file instead
    import shutil
    shutil.copy("/path/to/local/test.mp4", dest_path)
def upload_to_drive(file_path, filename):
    print(f"[mock] Would upload {filename}")
    return "mock-file-id", "https://drive.google.com/mock"