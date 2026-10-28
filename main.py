import os
import uuid
import shutil
import subprocess
import yt_dlp
from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

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
        return HTMLResponse(content=f.read())

# ၁။ Bilibili Video အချက်အလက် (Title, Duration, Thumbnail) လှမ်းယူခြင်း
@app.post("/bilibili/info")
async def get_bilibili_info(url: str = Form(...)):
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return JSONResponse({
                "title": info.get("title", "Bilibili Video"),
                "duration": info.get("duration", 60),
                "thumbnail": info.get("thumbnail", ""),
            })
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Bilibili Link ဖတ်မရပါ: {str(e)}")

# ၂။ Bilibili Video ကို Player ထဲတွင် ဖွင့်ကြည့်နိုင်ရန် Preview ဆွဲပေးခြင်း (360p/480p Fast Load)
@app.get("/bilibili/preview")
async def preview_bilibili(background_tasks: BackgroundTasks, url: str = Query(...)):
    job_id = str(uuid.uuid4())
    output_path = os.path.join(TEMP_DIR, f"{job_id}_preview.mp4")

    ydl_opts = {
        "format": " worst[ext=mp4]/bv*[height<=480][vcodec^=avc1]+ba[acodec^=mp4a]/b[height<=480]/b",
        "outtmpl": output_path,
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        if not os.path.exists(output_path):
            raise Exception("Preview file not generated")

        background_tasks.add_task(cleanup_files, output_path)
        return FileResponse(output_path, media_type="video/mp4")
    except Exception as e:
        cleanup_files(output_path)
        raise HTTPException(status_code=500, detail=f"Preview လုပ်မရပါ: {str(e)}")

# ၃။ Bilibili Video ကို ရွေးထားသော အချိန်အပိုင်းအခြားအတိုင်း တိုက်ရိုက်ဖြတ်ထုတ်ခြင်း
@app.post("/bilibili/cut")
async def cut_bilibili_video(
    background_tasks: BackgroundTasks,
    url: str = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...),
    quality: str = Form("1080"),
    mode: str = Form("lossless")
):
    job_id = str(uuid.uuid4())
    output_path = os.path.join(TEMP_DIR, f"{job_id}_bili_cut.mp4")

    # H.264 (avc1) + AAC ကို ဦးစားပေးရွေးချယ်ခြင်းဖြင့် ဖုန်းအားလုံးတွင် ကြည့်နိုင်ပြီး Size မကြီးစေပါ
    format_str = (
        f"bv*[height<={quality}][vcodec^=avc1]+ba[acodec^=mp4a]/"
        f"b[height<={quality}][ext=mp4]/"
        f"bv*[height<={quality}]+ba/b"
    )

    ydl_opts = {
        "format": format_str,
        "noplaylist": True,
        "quiet": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            title = info.get("title", "bilibili_video")
            safe_title = "".join(c for c in title if c.isalnum() or c in (" ", "-", "_")).strip()[:40] or "bilibili"

            headers = info.get("http_headers", {})
            if "Referer" not in headers:
                headers["Referer"] = "https://www.bilibili.com/"
            header_str = "".join([f"{k}: {v}\r\n" for k, v in headers.items()])

            req_formats = info.get("requested_formats")

            # Direct FFmpeg HTTP Range Cut (Video အစအဆုံး ဒေါင်းစရာမလိုဘဲ ဖြတ်မည့်အပိုင်းကိုသာ ဆွဲထုတ်ခြင်း)
            if req_formats and len(req_formats) >= 2:
                v_url = req_formats[0]["url"]
                a_url = req_formats[1]["url"]
                cmd = [
                    "ffmpeg", "-y",
                    "-headers", header_str, "-ss", start_time, "-to", end_time, "-i", v_url,
                    "-headers", header_str, "-ss", start_time, "-to", end_time, "-i", a_url,
                    "-map", "0:v:0", "-map", "1:a:0"
                ]
            else:
                stream_url = info.get("url") or req_formats[0]["url"]
                cmd = [
                    "ffmpeg", "-y",
                    "-headers", header_str, "-ss", start_time, "-to", end_time, "-i", stream_url
                ]

            if mode == "lossless":
                cmd += ["-c", "copy", "-avoid_negative_ts", "make_zero", output_path]
            else:
                cmd += ["-c:v", "libx264", "-crf", "23", "-preset", "fast", "-c:a", "aac", output_path]

            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            # အကယ်၍ Direct Stream Cut မရပါက yt-dlp --download-sections ဖြင့် အလိုအလျောက် ပြောင်းဖြတ်ပေးခြင်း
            if proc.returncode != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) < 1000:
                cleanup_files(output_path)
                fallback_cmd = [
                    "yt-dlp",
                    "--no-playlist",
                    "-f", format_str,
                    "--merge-output-format", "mp4",
                    "--download-sections", f"*{start_time}-{end_time}",
                    "-o", output_path,
                    url
                ]
                subprocess.run(fallback_cmd, check=True)

        if not os.path.exists(output_path):
            raise Exception("Video ဖြတ်ထုတ်၍ မရပါ။")

        background_tasks.add_task(cleanup_files, output_path)
        return FileResponse(
            output_path,
            media_type="video/mp4",
            filename=f"cut_{safe_title}.mp4"
        )

    except Exception as e:
        cleanup_files(output_path)
        raise HTTPException(status_code=500, detail=f"Bilibili Video ဖြတ်ရာတွင် အမှားရှိပါသည်: {str(e)}")

# ၄။ ဖုန်း/စက်ထဲမှ Local Video ဖိုင်များကို ဖြတ်ပေးခြင်း
@app.post("/cut")
async def cut_local_video(
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
            "-ss", start_time, "-to", end_time,
            "-i", input_path,
            "-c", "copy", "-avoid_negative_ts", "make_zero",
            output_path
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-ss", start_time, "-to", end_time,
            "-i", input_path,
            "-c:v", "libx264", "-crf", "23", "-preset", "fast", "-c:a", "aac",
            output_path
        ]

    process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    if process.returncode != 0 or not os.path.exists(output_path):
        cleanup_files(input_path, output_path)
        raise HTTPException(status_code=500, detail="Video ဖြတ်ရာတွင် အမှားအယွင်းရှိပါသည်။")

    background_tasks.add_task(cleanup_files, input_path, output_path)
    return FileResponse(output_path, media_type="video/mp4", filename=f"cut_{file.filename}")
