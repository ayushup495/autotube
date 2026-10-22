import os, json, asyncio, time, requests
import edge_tts
from PIL import Image, ImageDraw, ImageFont, ImageEnhance
from moviepy.editor import VideoFileClip, AudioFileClip, concatenate_videoclips, vfx
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

HI = os.environ.get("VIDEO_LANG", "hi") == "hi"
NICHE = os.environ.get("NICHE", "interesting facts")
GEMINI = os.environ["GEMINI_API_KEY"]
PIXABAY = os.environ["PIXABAY_API_KEY"]
VOICE = "hi-IN-MadhurNeural" if HI else "en-US-GuyNeural"
FONT = "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf" if HI else "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def retry(fn, tries=4):
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            print("retry", i, e)
            time.sleep(5 * (i + 1))
    raise RuntimeError("failed after retries")


def get_content():
    used = open("history.txt", encoding="utf-8").read() if os.path.exists("history.txt") else ""
    prompt = f"""You are a YouTube creator. Niche: {NICHE}.
Language: {"Hindi (Devanagari script)" if HI else "English"}.
Titles already used (do not repeat): {used}
Create ONE new video. Return JSON with keys:
title (max 70 chars, catchy),
description (150-250 words, end with 3 hashtags),
tags (list of 10),
script (voiceover, 150-200 words, plain text, no emojis),
keywords (list of 5 English stock-footage search terms),
thumb_text (max 4 words, same language as the video)."""
    models = ["gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-3-flash-preview",
              "gemini-2.5-flash-lite", "gemini-2.5-flash"]
    def call():
        last = None
        for m in models:
            try:
                r = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={GEMINI}",
                    json={"contents": [{"parts": [{"text": prompt}]}],
                          "generationConfig": {"responseMimeType": "application/json"}}, timeout=120)
                r.raise_for_status()
                d = json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])
                assert all(k in d for k in ["title", "description", "tags", "script", "keywords", "thumb_text"])
                print("used model", m)
                return d
            except Exception as e:
                print("model failed", m, e)
                last = e
        raise last
    return retry(call)

def make_voice(text):
    asyncio.run(edge_tts.Communicate(text, VOICE).save("voice.mp3"))


def download_clips(keywords):
    paths = []
    for i, kw in enumerate(keywords):
        def call():
            r = requests.get("https://pixabay.com/api/videos/",
                             params={"key": PIXABAY, "q": kw, "per_page": 5, "safesearch": "true"}, timeout=60)
            r.raise_for_status()
            v = r.json()["hits"][0]["videos"]
            link = v["large"]["url"] or v["medium"]["url"]
            p = f"clip{i}.mp4"
            open(p, "wb").write(requests.get(link, timeout=180).content)
            return p
        try:
            paths.append(retry(call, 3))
        except Exception as e:
            print("skip keyword", kw, e)
    assert paths, "no clips"
    return paths


def make_video(paths):
    audio = AudioFileClip("voice.mp3")
    per = audio.duration / len(paths)
    clips = []
    for p in paths:
        c = VideoFileClip(p).without_audio().resize((1920, 1080))
        c = c.fx(vfx.loop, duration=per) if c.duration < per else c.subclip(0, per)
        clips.append(c)
    v = concatenate_videoclips(clips).set_audio(audio)
    v.write_videofile("video.mp4", fps=24, codec="libx264", audio_codec="aac", logger=None)
    return VideoFileClip(paths[0]).get_frame(1)


def make_thumb(frame, text):
    img = Image.fromarray(frame).resize((1280, 720))
    img = ImageEnhance.Brightness(img).enhance(0.55)
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT, 130)
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if d.textlength(t, font=font) > 1100 and cur:
            lines.append(cur); cur = w
        else:
            cur = t
    lines.append(cur)
    y = 360 - len(lines) * 80
    for ln in lines:
        x = (1280 - d.textlength(ln, font=font)) / 2
        d.text((x, y), ln, font=font, fill="#FFE600", stroke_width=8, stroke_fill="black")
        y += 160
    img.convert("RGB").save("thumb.jpg", quality=90)


def upload(c):
    info = json.loads(os.environ["YT_TOKEN_JSON"])
    yt = build("youtube", "v3", credentials=Credentials.from_authorized_user_info(info))
    body = {"snippet": {"title": c["title"][:100], "description": c["description"],
                        "tags": c["tags"], "categoryId": "22",
                        "defaultLanguage": "hi" if HI else "en"},
            "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False}}
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload("video.mp4", chunksize=-1, resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    yt.thumbnails().set(videoId=resp["id"], media_body=MediaFileUpload("thumb.jpg")).execute()
    print("uploaded", resp["id"])


c = get_content()
make_voice(c["script"])
frame = make_video(download_clips(c["keywords"]))
make_thumb(frame, c["thumb_text"])
retry(lambda: upload(c), 2)
open("history.txt", "a", encoding="utf-8").write(c["title"] + "\n")
