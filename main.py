import os
import uuid
import shutil
import subprocess
from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks, HTTPException, Response
from fastapi.responses import FileResponse, HTMLResponse

app = FastAPI()

TEMP_DIR = "/tmp/video_cuts"
os.makedirs(TEMP_DIR, exist_ok=True)

def cleanup_files(*file_paths):
    for path in file_paths:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except Exception as e:
                print(f"Error deleting {path}: {e}")

@app.get("/", response_class=HTMLResponse)
async def home():
    with open("index.html", "r", encoding="utf-8") as f:
        content = f.read()
    return HTMLResponse(content=content)

@app.post("/cut")
async def cut_video(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    start_time: str = Form(...),
    end_time: str = Form(...),
    mode: str = Form("lossless")
):
    job_id = str(uuid.uuid4())
    ext = os.path.splitext(file.filename)[1] or ".mp4"
    input_path = os.path.join(TEMP_DIR, f"{job_id}_in{ext}")
    output_path = os.path.join(TEMP_DIR, f"{job_id}_out{ext}")

    try:
        with open(input_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        cleanup_files(input_path)
        raise HTTPException(status_code=500, detail=f"Upload error: {str(e)}")

    if mode == "lossless":
        cmd = [
            "ffmpeg", "-y",
            "-ss", start_time,
            "-to", end_time,
            "-i", input_path,
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            output_path
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-ss", start_time,
            "-to", end_time,
            "-i", input_path,
            "-c:v", "libx264", "-crf", "23", "-preset", "fast",
            "-c:a", "aac",
            output_path
        ]

    process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    if process.returncode != 0 or not os.path.exists(output_path):
        cleanup_files(input_path, output_path)
        raise HTTPException(status_code=500, detail="Video ဖြတ်ရာတွင် အမှားအယွင်းရှိပါသည်။")

    background_tasks.add_task(cleanup_files, input_path, output_path)

    return FileResponse(
        output_path,
        media_type="video/mp4",
        filename=f"cut_{file.filename}"
    )
