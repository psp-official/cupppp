import os
import re
import uuid
import shutil
import subprocess
import yt_dlp
from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

app = FastAPI()

TEMP_DIR = "/tmp/video_cuts"
os.makedirs(TEMP_DIR, exist_ok=True)

class UrlRequest(BaseModel):
    url: str

def cleanup_files(*file_paths):
    for path in file_paths:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except Exception as e:
                print(f"Error deleting {path}: {e}")

def extract_youtube_id(url: str):
    pattern = r"(?:v=|\/embed\/|\/watch\?v=|youtu\.be\/|\/shorts\/)([0-9A-Za-z_-]{11})"
    match = re.search(pattern, url)
    return match.group(1) if match else None

@app.get("/", response_class=HTMLResponse)
async def home():
    with open("index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

# ၁။ Link ထည့်ပြီး "Load video" နှိပ်သည့်အခါ ဗီဒီယိုအချက်အလက် လှမ်းယူသည့် Endpoint
@app.post("/load-url")
async def load_url(req: UrlRequest):
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="Link ထည့်သွင်းပေးပါ။")

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "format": "best[ext=mp4]/best",
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            duration = float(info.get("duration") or 60)
            title = info.get("title") or "video"
            stream_url = info.get("url")
            filesize_bytes = info.get("filesize") or info.get("filesize_approx") or 0
            filesize_mb = round(filesize_bytes / (1024 * 1024), 1) if filesize_bytes else 0

            return JSONResponse({
                "title": title,
                "duration": duration,
                "stream_url": stream_url,
                "youtube_id": extract_youtube_id(url),
                "filesize_mb": filesize_mb
            })
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"ဤ Link မှ Video ကို ဖတ်၍မရပါ: {str(e)}")

# ၂။ Link မှ Video ကို တိုက်ရိုက် ဖြတ်ပေးသည့် Endpoint
@app.post("/cut-url")
async def cut_url_video(
    background_tasks: BackgroundTasks,
    url: str = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...),
    mode: str = Form("lossless")
):
    job_id = str(uuid.uuid4())
    temp_download_path = os.path.join(TEMP_DIR, f"{job_id}_dl.mp4")
    output_path = os.path.join(TEMP_DIR, f"{job_id}_out.mp4")

    try:
        # အဆင့် (၁) - Direct Stream URL ထုတ်ယူပြီး လိုအပ်သည့် အပိုင်းကိုသာ FFmpeg ဖြင့် တိုက်ရိုက်ဖြတ်ခြင်း
        ydl_opts = {
            "quiet": True,
            "noplaylist": True,
            "format": "best[ext=mp4]/best",
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            stream_url = info.get("url")
            safe_title = re.sub(r'[^\w\-_\. ]', '_', info.get("title", "video"))[:50]

        cut_success = False
        if stream_url:
            if mode == "lossless":
                cmd = [
                    "ffmpeg", "-y",
                    "-ss", start_time,
                    "-to", end_time,
                    "-i", stream_url,
                    "-c", "copy",
                    "-avoid_negative_ts", "make_zero",
                    output_path
                ]
            else:
                cmd = [
                    "ffmpeg", "-y",
                    "-ss", start_time,
                    "-to", end_time,
                    "-i", stream_url,
                    "-c:v", "libx264", "-crf", "23", "-preset", "fast",
                    "-c:a", "aac",
                    output_path
                ]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
                cut_success = True

        # အဆင့် (၂) - အချို့ Website များတွင် တိုက်ရိုက် Stream ဖြတ်မရပါက yt-dlp ဖြင့် အရင်ဆွဲပြီး ဖြတ်ပေးခြင်း (Auto Fallback)
        if not cut_success:
            dl_opts = {
                "quiet": True,
                "noplaylist": True,
                "format": "bestVideo[ext=mp4]+bestAudio[ext=m4a]/best[ext=mp4]/best",
                "merge_output_format": "mp4",
                "outtmpl": temp_download_path,
            }
            with yt_dlp.YoutubeDL(dl_opts) as ydl:
                ydl.download([url])

            if mode == "lossless":
                cmd2 = [
                    "ffmpeg", "-y",
                    "-ss", start_time,
                    "-to", end_time,
                    "-i", temp_download_path,
                    "-c", "copy",
                    "-avoid_negative_ts", "make_zero",
                    output_path
                ]
            else:
                cmd2 = [
                    "ffmpeg", "-y",
                    "-ss", start_time,
                    "-to", end_time,
                    "-i", temp_download_path,
                    "-c:v", "libx264", "-crf", "23", "-preset", "fast",
                    "-c:a", "aac",
                    output_path
                ]
            proc2 = subprocess.run(cmd2, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if proc2.returncode != 0 or not os.path.exists(output_path):
                raise Exception("Video ဖြတ်တောက်ရာတွင် အမှားအယွင်းရှိပါသည်။")

        background_tasks.add_task(cleanup_files, temp_download_path, output_path)
        return FileResponse(
            output_path,
            media_type="video/mp4",
            filename=f"cut_{safe_title}.mp4"
        )

    except Exception as e:
        cleanup_files(temp_download_path, output_path)
        raise HTTPException(status_code=500, detail=f"Link မှ Video ဖြတ်မရပါ: {str(e)}")

# ၃။ ဖုန်း/ကွန်ပျူတာထဲမှ File တင်ပြီး ဖြတ်သည့် Endpoint
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
