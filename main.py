import os, sys, re, json, asyncio, time, random, subprocess, datetime, requests
import edge_tts
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageOps
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

HI = os.environ.get("VIDEO_LANG", "hi").strip().lower() == "hi"
GEMINI = os.environ.get("GEMINI_API_KEY", "")
PIXABAY = os.environ.get("PIXABAY_API_KEY", "")
VOICE = "hi-IN-MadhurNeural" if HI else "en-US-GuyNeural"
LANG_NAME = "Hindi (Devanagari script)" if HI else "English"
MODELS = ["gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-3-flash-preview",
          "gemini-2.5-flash-lite", "gemini-2.5-flash"]
HIST = "history.txt"
DONE = "last_run.txt"
try:
    LONG_MIN = max(3, min(25, int((os.environ.get("LONG_MINUTES") or "4").strip())))
except ValueError:
    LONG_MIN = 4
IG_USER = os.environ.get("IG_USER_ID", "").strip()
IG_TOKEN = os.environ.get("IG_TOKEN", "").strip()
GRAPH = "https://graph.facebook.com"

CATS = [
    {"en": "Cars & Automobiles", "hi": "कार और गाड़ियां", "be": "CARS", "bh": "कार", "color": "#E53935",
     "fb": ["car driving", "car engine", "highway traffic"]},
    {"en": "Space & Rockets", "hi": "अंतरिक्ष और रॉकेट", "be": "SPACE", "bh": "अंतरिक्ष", "color": "#5C6BC0",
     "fb": ["rocket launch", "galaxy stars", "planet earth space"]},
    {"en": "Aviation & Aeroplanes", "hi": "विमान और एविएशन", "be": "AVIATION", "bh": "विमान", "color": "#039BE5",
     "fb": ["airplane flying", "airport runway", "clouds sky"]},
    {"en": "AI & Machine Learning", "hi": "एआई और मशीन लर्निंग", "be": "AI & ML", "bh": "एआई", "color": "#8E24AA",
     "fb": ["artificial intelligence", "robot", "data network"]},
    {"en": "Science Explained", "hi": "विज्ञान की बातें", "be": "SCIENCE", "bh": "विज्ञान", "color": "#43A047",
     "fb": ["science laboratory", "microscope", "chemistry"]},
    {"en": "Technology & Gadgets", "hi": "टेक्नोलॉजी और गैजेट्स", "be": "TECH", "bh": "टेक", "color": "#FB8C00",
     "fb": ["technology", "circuit board", "smartphone"]},
    {"en": "Engineering & Machines", "hi": "इंजीनियरिंग और मशीनें", "be": "ENGINEERING", "bh": "इंजीनियरिंग", "color": "#546E7A",
     "fb": ["factory machine", "construction site", "bridge"]},
    {"en": "Inventions & History", "hi": "आविष्कार और इतिहास", "be": "INVENTIONS", "bh": "आविष्कार", "color": "#D81B60",
     "fb": ["old machine", "ancient ruins", "library books"]},
    {"en": "Earth & Oceans", "hi": "पृथ्वी और महासागर", "be": "EARTH", "bh": "पृथ्वी", "color": "#00897B",
     "fb": ["ocean waves", "mountains", "volcano"]},
    {"en": "Animals & Nature", "hi": "जानवर और प्रकृति", "be": "NATURE", "bh": "प्रकृति", "color": "#7CB342",
     "fb": ["wild animals", "forest", "birds flying"]},
]

DEV_FONTS = ["/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf"]
LAT_FONTS = ["/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]


def retry(fn, tries=4, wait=5):
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            last = e
            print("retry", i, repr(e)[:300])
            time.sleep(wait * (i + 1))
    raise RuntimeError(f"failed after retries: {last!r}"[:500])


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-1500:])
        raise RuntimeError("command failed: " + cmd[0])


def duration(path):
    out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                   "-of", "default=nw=1:nk=1", path])
    return float(out.strip())


# ---------- history ----------
def read_history():
    rows, used = [], []
    if os.path.exists(HIST):
        for line in open(HIST, encoding="utf-8").read().splitlines():
            line = line.strip()
            if not line:
                continue
            p = line.split("|", 2)
            if len(p) == 3:
                rows.append(p)
                used.append(p[2])
            else:
                used.append(line)
    return rows, used


def add_history(kind, cat, title):
    with open(HIST, "a", encoding="utf-8") as f:
        f.write(f"{kind}|{cat['en']}|{title.replace('|', '/')}\n")


def today_utc():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def done_today(kind):
    # Backup schedule runs must not repeat work that is already finished today.
    if os.environ.get("GITHUB_EVENT_NAME") != "schedule" or not os.path.exists(DONE):
        return False
    return f"{today_utc()} {kind}" in open(DONE).read().splitlines()


def mark_done(kind):
    with open(DONE, "a") as f:
        f.write(f"{today_utc()} {kind}\n")


def pick_cat(kind, rows):
    n = sum(1 for r in rows if r[0] == kind)
    shift = 0 if kind == "long" else len(CATS) // 2
    return CATS[(n + shift) % len(CATS)]


# ---------- content ----------
def gemini(prompt):
    last = None
    for m in MODELS:
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={GEMINI}",
                json={"contents": [{"parts": [{"text": prompt}]}],
                      "generationConfig": {"responseMimeType": "application/json", "temperature": 0.8}},
                timeout=180)
            r.raise_for_status()
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            print("used model", m)
            return json.loads(text)
        except Exception as e:
            print("model failed", m, repr(e)[:200])
            last = e
    raise last


def clean(d, kind, minw=None):
    need = {"title", "description", "tags", "script", "keywords", "thumb_text"}
    assert isinstance(d, dict) and need <= set(d), "missing keys"
    d["title"] = str(d["title"]).replace("<", "").replace(">", "").strip()[:95]
    d["description"] = str(d["description"]).replace("<", "").replace(">", "").strip()[:4500]
    d["script"] = " ".join(str(d["script"]).split())
    d["thumb_text"] = " ".join(str(d["thumb_text"]).split())[:40]
    d["keywords"] = [str(k).strip() for k in d["keywords"] if str(k).strip()][:max(16, int(LONG_MIN * 3.5) + 4)]
    tags, total = [], 0
    for t in d["tags"]:
        t = str(t).replace("<", "").replace(">", "").strip()
        if t and total + len(t) < 400:
            tags.append(t)
            total += len(t) + 1
    d["tags"] = tags[:15]
    assert d["title"] and d["description"], "empty title or description"
    assert len(d["script"].split()) >= (minw or (int(LONG_MIN * 135 * 0.5) if kind == "long" else 50)), "script too short"
    assert len(d["keywords"]) >= 4, "too few keywords"
    if kind == "short" and "#shorts" not in d["description"].lower():
        d["description"] += "\n\n#Shorts"
    return d


RULES = ("Rules: educational and factually accurate; use only well-known, verifiable facts; do not invent statistics, dates or quotes; "
         "no medical, financial or political claims; no brand logos or copyrighted material; clear, friendly and engaging. "
         "The script is plain spoken text only: no emojis, no stage directions, no markdown, no brackets."
         + (" Write technical English terms in Devanagari so the voice pronounces them correctly." if HI else ""))


def get_content_sections(cat, used):
    # Long videos (more than 6 minutes): plan first, then write the script section by section.
    n = max(4, round(LONG_MIN / 2.5))
    per = int(LONG_MIN * 135 / n)
    thumb_note = " (pure Devanagari, write English words like AI in Devanagari)" if HI else ""
    head_prompt = f"""You are a professional educational YouTube creator.
Topic category: {cat['en']}. Language: {LANG_NAME}.
Plan ONE long educational video of about {LONG_MIN} minutes about a single main subject.
Titles already used (never repeat or closely copy): {' | '.join(used[-60:])}
{RULES}
Return ONLY JSON with these keys:
title: max 70 characters, curiosity-driven, honest
description: 120-200 words, first line is a hook, end with 3 hashtags
tags: list of 10 short tags
thumb_text: 2 to 4 punchy words{thumb_note}
sections: list of exactly {n} short section headings in a logical teaching order, each a different part of the subject"""

    def plan():
        h = gemini(head_prompt)
        assert isinstance(h, dict) and {"title", "description", "tags", "thumb_text", "sections"} <= set(h), "bad plan"
        secs = [str(x).strip() for x in h["sections"] if str(x).strip()]
        assert len(secs) >= 3, "too few sections"
        h["sections"] = secs[:n]
        return h
    head = retry(plan, 4, 8)
    secs = head["sections"]
    texts, kws = [], []
    for i, sec in enumerate(secs):
        if i == 0:
            note = "Start with a strong hook sentence and say what the video will cover."
        elif i == len(secs) - 1:
            note = "Finish with a short summary and a short invitation to subscribe."
        else:
            note = "Continue smoothly from the previous part. Do not greet and do not conclude."
        tail = (" ".join(texts))[-300:]
        p = f"""You are writing part {i + 1} of {len(secs)} of the spoken script of the educational video "{head['title']}".
Language: {LANG_NAME}.
All parts: {' | '.join(secs)}
The end of what is already written: {tail or '(nothing yet)'}
Write ONLY part {i + 1}, "{sec}", of about {per} words. {note}
{RULES}
Return ONLY JSON with two keys:
text: the spoken script of this part
keywords: list of 6 different English stock-footage search terms (1-3 words each), concrete things that can be filmed, in the order of this part"""

        def part():
            r = gemini(p)
            assert isinstance(r, dict) and "text" in r and "keywords" in r, "bad part"
            t = " ".join(str(r["text"]).split())
            k = [str(x).strip() for x in r["keywords"] if str(x).strip()]
            assert len(t.split()) >= per * 0.5, "part too short"
            assert len(k) >= 3, "too few keywords"
            return t, k
        t, k = retry(part, 4, 8)
        texts.append(t)
        kws += k
        print("part", i + 1, "of", len(secs), "words:", len(t.split()))
        time.sleep(3)
    seen, keywords = set(), []
    for k in kws:
        if k.lower() not in seen:
            seen.add(k.lower())
            keywords.append(k)
    d = {"title": head["title"], "description": head["description"], "tags": head["tags"],
         "thumb_text": head["thumb_text"], "script": " ".join(texts), "keywords": keywords}
    return clean(d, "long", minw=int(LONG_MIN * 135 * 0.6))


def get_content(kind, cat, used):
    if kind == "long" and LONG_MIN > 6:
        return get_content_sections(cat, used)
    if kind == "long":
        fmt = f"a {LONG_MIN - 1} to {LONG_MIN} minute educational explainer video"
        words, nk = f"{int(LONG_MIN * 115)}-{int(LONG_MIN * 135)}", max(14, int(LONG_MIN * 3.5))
    else:
        fmt, words, nk = "a YouTube Short of about 40 seconds", "90-110", 6
    thumb_note = " (pure Devanagari, write English words like AI in Devanagari)" if HI else ""
    prompt = f"""You are a professional educational YouTube creator.
Topic category: {cat['en']}. Language: {LANG_NAME}.
Format: {fmt}.
Titles already used (never repeat or closely copy): {' | '.join(used[-60:])}
{RULES} The first sentence is a strong hook; end with a short invitation to subscribe.
Return ONLY JSON with these keys:
title: max 70 characters, curiosity-driven, honest
description: 120-200 words, first line is a hook, end with 3 hashtags
tags: list of 10 short tags
script: {words} words
keywords: list of {nk} different English stock-footage search terms (1-3 words each), concrete things that can be filmed, in the order of the script
thumb_text: 2 to 4 punchy words{thumb_note}"""
    return retry(lambda: clean(gemini(prompt), kind), 4, 8)


def split_text(text, limit=1200):
    parts, cur = [], ""
    for sent in re.split(r"(?<=[.!?\u0964])\s+", text):
        if cur and len(cur) + len(sent) + 1 > limit:
            parts.append(cur)
            cur = sent
        else:
            cur = (cur + " " + sent).strip()
    if cur:
        parts.append(cur)
    return parts


def make_voice(text, path, rate):
    parts = split_text(text)
    if len(parts) == 1:
        asyncio.run(edge_tts.Communicate(text, VOICE, rate=rate).save(path))
        return
    files = []
    for i, p in enumerate(parts):
        f = f"{path}.part{i}.mp3"
        retry(lambda: asyncio.run(edge_tts.Communicate(p, VOICE, rate=rate).save(f)), 4, 6)
        files.append(f)
    lst = path + ".list.txt"
    with open(lst, "w") as fh:
        fh.write("".join(f"file '{f}'\n" for f in files))
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c:a", "libmp3lame", "-q:a", "4", path])
    for f in files + [lst]:
        try:
            os.remove(f)
        except OSError:
            pass


# ---------- clips ----------
def fetch_clips(keywords, fallback, need, big, prefix):
    paths, seen = [], set()

    def get_one(kw):
        r = requests.get("https://pixabay.com/api/videos/",
                         params={"key": PIXABAY, "q": kw[:90], "per_page": 15, "safesearch": "true"},
                         timeout=60)
        r.raise_for_status()
        hits = [h for h in r.json().get("hits", []) if h["id"] not in seen and h.get("duration", 10) >= 4]
        if not hits:
            return None
        h = random.choice(hits[:5])
        v = h["videos"]
        order = ["large", "medium", "small", "tiny"] if big else ["medium", "small", "large", "tiny"]
        url = next(v[k]["url"] for k in order if k in v and v[k].get("url"))
        p = f"{prefix}{len(paths)}.mp4"
        with requests.get(url, stream=True, timeout=(30, 300)) as resp:
            resp.raise_for_status()
            with open(p, "wb") as f:
                for chunk in resp.iter_content(1 << 20):
                    f.write(chunk)
        seen.add(h["id"])
        return p

    for kw in list(keywords) + list(fallback):
        if len(paths) >= need:
            break
        try:
            p = retry(lambda: get_one(kw), 3, 4)
            if p:
                paths.append(p)
            else:
                print("no clip for", kw)
        except Exception as e:
            print("skip keyword", kw, repr(e)[:200])
    assert len(paths) >= 3, "not enough clips"
    return paths


def build_video(clips, voice, out, w, h, tag):
    dur = duration(voice)
    per = (dur + 0.8) / len(clips)
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
          f"fps=30,setsar=1,format=yuv420p")
    segs = []
    for i, c in enumerate(clips):
        s = f"{tag}_seg{i}.mp4"
        run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", c, "-t", f"{per:.3f}", "-vf", vf, "-an",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-r", "30", s])
        segs.append(s)
    lst = f"{tag}_list.txt"
    with open(lst, "w") as f:
        f.write("".join(f"file '{s}'\n" for s in segs))
    joined = f"{tag}_joined.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", joined])
    run(["ffmpeg", "-y", "-i", joined, "-i", voice, "-map", "0:v", "-map", "1:a",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", out])
    for s in segs + [lst, joined]:
        try:
            os.remove(s)
        except OSError:
            pass


# ---------- thumbnail ----------
def find_font(cands):
    for p in cands:
        if os.path.exists(p):
            return p
    raise RuntimeError("font not found: " + cands[0])


def is_dev(ch):
    return "\u0900" <= ch <= "\u097F" or ch in "\u200c\u200d"


def runs(text):
    out, cur = [], None
    for ch in text:
        t = "d" if is_dev(ch) else ("l" if ch.isalpha() else None)
        if t is None:
            t = cur or "l"
        if t == cur:
            out[-1][1] += ch
        else:
            out.append([t, ch])
            cur = t
    return out


def text_w(d, text, fd, fl):
    return sum(d.textlength(s, font=(fd if t == "d" else fl)) for t, s in runs(text))


def draw_text(d, x, y, text, fd, fl, fill, sw):
    for t, s in runs(text):
        f = fd if t == "d" else fl
        d.text((x, y), s, font=f, fill=fill, anchor="ls", stroke_width=sw, stroke_fill="black")
        x += d.textlength(s, font=f)


def wrap(d, text, fd, fl, maxw):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if text_w(d, t, fd, fl) > maxw and cur:
            lines.append(cur)
            cur = w
        else:
            cur = t
    lines.append(cur)
    return lines


def make_thumb(frame, text, cat, out):
    W, H = 1280, 720
    img = Image.open(frame).convert("RGB")
    img = ImageOps.fit(img, (W, H), Image.Resampling.LANCZOS)
    img = ImageEnhance.Color(img).enhance(1.25)
    img = ImageEnhance.Contrast(img).enhance(1.1)
    img = ImageEnhance.Brightness(img).enhance(0.9)
    grad = ImageOps.invert(Image.linear_gradient("L").rotate(90, expand=True).resize((960, H)))
    grad = grad.point(lambda v: int(v * 0.93))
    img.paste(Image.new("RGB", (960, H), (0, 0, 0)), (0, 0), grad)
    d = ImageDraw.Draw(img)
    devp, latp = find_font(DEV_FONTS), find_font(LAT_FONTS)
    maxw, x0 = 760, 70
    text = text.upper() if not HI else text
    size = 190
    while True:
        fd, fl = ImageFont.truetype(devp, size), ImageFont.truetype(latp, size)
        lines = wrap(d, text, fd, fl, maxw)
        fits = all(text_w(d, ln, fd, fl) <= maxw for ln in lines) and len(lines) <= 3
        lh = int(size * 1.28)
        if (fits and lh * len(lines) <= 470) or size <= 70:
            break
        size -= 6
    top = 380 - (lh * len(lines)) // 2 + int(size * 0.98)
    for i, ln in enumerate(lines):
        col = "#FFD400" if i == len(lines) - 1 else "white"
        draw_text(d, x0, top + i * lh, ln, fd, fl, col, max(6, size // 22))
    badge = cat["bh"] if HI else cat["be"]
    bf_d, bf_l = ImageFont.truetype(devp, 44), ImageFont.truetype(latp, 44)
    bw = int(text_w(d, badge, bf_d, bf_l)) + 56
    d.rounded_rectangle((x0, 56, x0 + bw, 130), radius=16, fill=cat["color"])
    draw_text(d, x0 + 28, 106, badge, bf_d, bf_l, "white", 0)
    d.rectangle((0, H - 16, W, H), fill=cat["color"])
    for q in (92, 85, 78, 70):
        img.save(out, "JPEG", quality=q)
        if os.path.getsize(out) < 1_900_000:
            break


# ---------- YouTube ----------
def yt_client():
    info = json.loads(os.environ["YT_TOKEN_JSON"])
    return build("youtube", "v3", credentials=Credentials.from_authorized_user_info(info))


def publish_time():
    now = datetime.datetime.now(datetime.timezone.utc)
    t = now.replace(hour=2, minute=30, second=0, microsecond=0)
    if t - now > datetime.timedelta(minutes=4):
        return t.strftime("%Y-%m-%dT%H:%M:%SZ")
    return None


def upload(yt, path, c, kind, when):
    status = {"privacyStatus": "private" if when else "public", "selfDeclaredMadeForKids": False}
    if when:
        status["publishAt"] = when
    body = {"snippet": {"title": c["title"], "description": c["description"], "tags": c["tags"],
                        "categoryId": "27", "defaultLanguage": "hi" if HI else "en"},
            "status": status}
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(path, chunksize=32 * 1024 * 1024, resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk(num_retries=5)
    print("uploaded", kind, resp["id"], "scheduled for " + when if when else "public now")
    return resp["id"]


def playlist_id(yt, title):
    token = None
    while True:
        r = yt.playlists().list(part="snippet", mine=True, maxResults=50, pageToken=token).execute()
        for it in r.get("items", []):
            if it["snippet"]["title"] == title:
                return it["id"]
        token = r.get("nextPageToken")
        if not token:
            break
    r = yt.playlists().insert(part="snippet,status", body={
        "snippet": {"title": title, "description": title},
        "status": {"privacyStatus": "public"}}).execute()
    return r["id"]


def add_to_playlist(yt, title, vid):
    pid = playlist_id(yt, title)
    yt.playlistItems().insert(part="snippet", body={"snippet": {
        "playlistId": pid, "resourceId": {"kind": "youtube#video", "videoId": vid}}}).execute()
    print("added to playlist", title)


# ---------- Instagram ----------
def ig_prepare(path, caption):
    size = os.path.getsize(path)
    r = requests.post(f"{GRAPH}/{IG_USER}/media", data={
        "media_type": "REELS", "upload_type": "resumable", "share_to_feed": "true",
        "caption": caption, "access_token": IG_TOKEN}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError("IG create failed: " + r.text[:300])
    j = r.json()
    cid, uri = j["id"], j["uri"]
    with open(path, "rb") as f:
        up = requests.post(uri, headers={"Authorization": f"OAuth {IG_TOKEN}", "offset": "0",
                                         "file_size": str(size)}, data=f, timeout=900)
    if up.status_code != 200:
        raise RuntimeError("IG upload failed: " + up.text[:300])
    for _ in range(60):
        st = requests.get(f"{GRAPH}/{cid}", params={"fields": "status_code,status",
                                                    "access_token": IG_TOKEN}, timeout=60).json()
        code = st.get("status_code")
        if code == "FINISHED":
            return cid
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError("IG processing failed: " + str(st)[:300])
        time.sleep(10)
    raise RuntimeError("IG processing timeout")


def ig_publish(cid):
    r = requests.post(f"{GRAPH}/{IG_USER}/media_publish",
                      data={"creation_id": cid, "access_token": IG_TOKEN}, timeout=120)
    if r.status_code != 200:
        raise RuntimeError("IG publish failed: " + r.text[:300])
    print("instagram posted", r.json().get("id"))


def wait_until_publish():
    when = publish_time()
    if not when:
        return
    target = datetime.datetime.strptime(when, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    secs = (target - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
    if 0 < secs < 4 * 3600:
        print("waiting", int(secs), "seconds for 8:00 AM IST")
        time.sleep(secs)


# ---------- one video ----------
def produce(kind, yt):
    if done_today(kind):
        print(kind, "already published today, skipping")
        return None
    rows, used = read_history()
    cat = pick_cat(kind, rows)
    print("==", kind, "| category:", cat["en"])
    c = get_content(kind, cat, used)
    print("title:", c["title"])
    voice = f"{kind}_voice.mp3"
    for rate in (["+0%"] if kind == "long" else ["+8%", "+18%", "+30%"]):
        retry(lambda: make_voice(c["script"], voice, rate), 4, 6)
        if kind == "long" or duration(voice) <= 58:
            break
    big = kind == "short"
    need = len(c["keywords"])
    clips = fetch_clips(c["keywords"], cat["fb"], need, big, f"{kind}_clip")
    out = f"{kind}.mp4"
    w, h = (1920, 1080) if kind == "long" else (1080, 1920)
    build_video(clips, voice, out, w, h, kind)
    thumb = None
    if kind == "long":
        run(["ffmpeg", "-y", "-ss", "1", "-i", clips[0], "-frames:v", "1", "-q:v", "2", "frame.jpg"])
        thumb = "thumb.jpg"
        make_thumb("frame.jpg", c["thumb_text"], cat, thumb)
    vid = retry(lambda: upload(yt, out, c, kind, publish_time()), 2, 20)
    add_history(kind, cat, c["title"])
    mark_done(kind)
    if thumb:
        try:
            yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(thumb)).execute()
            print("thumbnail set")
        except Exception as e:
            print("THUMBNAIL FAILED", repr(e)[:300])
    try:
        pl = ("शॉर्ट्स" if HI else "Daily Shorts") if kind == "short" else (cat["hi"] if HI else cat["en"])
        add_to_playlist(yt, pl, vid)
    except Exception as e:
        print("PLAYLIST FAILED", repr(e)[:300])
    for p in clips + [voice] + ([] if kind == "short" else [out]):
        try:
            os.remove(p)
        except OSError:
            pass
    if kind == "short":
        return out, (c["title"] + "\n\n" + c["description"])[:2000]
    return None


def main():
    for k in ("GEMINI_API_KEY", "PIXABAY_API_KEY", "YT_TOKEN_JSON"):
        if not os.environ.get(k):
            sys.exit("missing secret: " + k)
    yt = yt_client()
    errors, short = [], None
    for kind in ("long", "short"):
        try:
            r = produce(kind, yt)
            if kind == "short":
                short = r
        except Exception as e:
            print("FAILED", kind, repr(e)[:500])
            errors.append(kind)
    if short and IG_USER and IG_TOKEN:
        try:
            cid = retry(lambda: ig_prepare(short[0], short[1]), 3, 30)
            wait_until_publish()
            ig_publish(cid)
            mark_done("instagram")
        except Exception as e:
            print("FAILED instagram", repr(e)[:500])
            errors.append("instagram")
    elif short:
        print("instagram skipped: IG_USER_ID / IG_TOKEN not set")
    if errors:
        sys.exit("failed: " + ", ".join(errors))


if __name__ == "__main__":
    main()
