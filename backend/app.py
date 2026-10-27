
# File: heygen-invoice-demo/backend/app.py

import base64
import binascii
import io
import logging
import os
import subprocess
import threading
import uuid
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_file, send_from_directory
from flask_cors import CORS
from openai import OpenAI
from pinecone import Pinecone
from werkzeug.utils import secure_filename

load_dotenv()

# ---------------------------------------------------------
# Application configuration
# ---------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIST = (BASE_DIR.parent / "frontend-vite" / "dist").resolve()

app = Flask(
    __name__,
    static_folder=str(FRONTEND_DIST),
    static_url_path=""
)

app.config["MAX_CONTENT_LENGTH"] = 3 * 1024 * 1024

CORS(
    app,
    origins=[
        "https://infy-heygen-demo1.onrender.com",
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:5000",
    ],
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("heygen-invoice-demo")

# ---------------------------------------------------------
# OpenAI and Pinecone
# ---------------------------------------------------------

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
PINECONE_INDEX_NAME = os.getenv("PINECONE_INDEX_NAME")

openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

pinecone_index = None

if PINECONE_API_KEY and PINECONE_INDEX_NAME:
    try:
        pinecone_client = Pinecone(api_key=PINECONE_API_KEY)
        pinecone_index = pinecone_client.Index(PINECONE_INDEX_NAME)
    except Exception:
        logger.exception("Could not initialize Pinecone.")

SYSTEM_PROMPT = """You are an internal assistant for ABC Company.

You answer questions ONLY based on the context provided below.

You have knowledge about two areas:
1. Employee Onboarding — first day, working hours, leave, dress code, etc.
2. Invoice Processing — ERP logging, PO matching, approvals, payments, etc.

If the answer is not in the provided context, say exactly:
"I could not find that in the company guide. Please speak to your HR representative or Finance Manager."

Never use general knowledge. Always be clear and concise.
Do NOT add a source or citation at the end of your answer.
"""

FALLBACK_ANSWER = (
    "I could not find that in the company guide. "
    "Please speak to your HR representative or Finance Manager."
)

# ---------------------------------------------------------
# SadTalker configuration
# ---------------------------------------------------------

SADTALKER_ROOT = Path(
    os.getenv(
        "SADTALKER_ROOT",
        str(BASE_DIR.parent / "SadTalker")
    )
).resolve()

SADTALKER_PYTHON = Path(
    os.getenv(
        "SADTALKER_PYTHON",
        r"D:\SadTalker-Setup\venv312\Scripts\python.exe"
    )
).resolve()

SADTALKER_RESULTS = Path(
    os.getenv(
        "SADTALKER_RESULTS",
        r"D:\SadTalker-Setup\results"
    )
).resolve()

SADTALKER_AUDIO = Path(
    os.getenv(
        "SADTALKER_AUDIO",
        r"D:\SadTalker-Setup\audio"
    )
).resolve()

SADTALKER_CHECKPOINTS = SADTALKER_ROOT / "checkpoints"

DEFAULT_PORTRAIT = (
    SADTALKER_ROOT
    / "examples"
    / "source_image"
    / "full3.png"
)

SADTALKER_INFERENCE = SADTALKER_ROOT / "inference.py"

SADTALKER_TIMEOUT_SECONDS = int(
    os.getenv("SADTALKER_TIMEOUT_SECONDS", "600")
)

SADTALKER_MAX_ANSWER_LENGTH = 1500
MAX_PORTRAIT_BYTES = 2 * 1024 * 1024

SADTALKER_RESULTS.mkdir(parents=True, exist_ok=True)
SADTALKER_AUDIO.mkdir(parents=True, exist_ok=True)

# Jobs are stored in memory for this local implementation.
sadtalker_jobs = {}
sadtalker_jobs_lock = threading.Lock()

# Only one SadTalker inference can use the GPU at a time.
sadtalker_gpu_lock = threading.Lock()

# ---------------------------------------------------------
# Shared helper functions
# ---------------------------------------------------------

def get_json_body():
    """Return a JSON request body or an empty dictionary."""
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def get_relevant_context(question):
    """Retrieve relevant company-guide content from Pinecone."""

    if openai_client is None:
        raise RuntimeError("OPENAI_API_KEY is not configured.")

    if pinecone_index is None:
        raise RuntimeError("Pinecone is not configured.")

    response = openai_client.embeddings.create(
        input=question,
        model="text-embedding-3-large"
    )

    query_embedding = response.data[0].embedding

    results = pinecone_index.query(
        vector=query_embedding,
        top_k=3,
        include_metadata=True
    )

    context_parts = []
    source = "Infylearn Guide v1.0"

    for match in results.matches:
        if match.score > 0.2 and match.metadata:
            answer_text = match.metadata.get("answer")

            if answer_text:
                context_parts.append(answer_text)

            source = match.metadata.get("source", source)

    return "\n\n".join(context_parts), source


def generate_answer(question, context):
    """Generate a concise answer using the company-guide context."""

    if openai_client is None:
        raise RuntimeError("OPENAI_API_KEY is not configured.")

    response = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT
            },
            {
                "role": "user",
                "content": (
                    f"ONBOARDING CONTEXT:\n{context}\n\n"
                    f"QUESTION: {question}"
                )
            }
        ],
        max_tokens=400,
        temperature=0
    )

    return (
        response.choices[0].message.content or FALLBACK_ANSWER
    ).strip()


def update_sadtalker_job(job_id, **updates):
    """Update a job safely from a worker thread."""

    with sadtalker_jobs_lock:
        job = sadtalker_jobs.get(job_id)

        if job is not None:
            job.update(updates)


def validate_portrait_data(portrait_data, portrait_path):
    """
    Decode an optional base64 image data URL.

    Pillow is imported only when a custom portrait is supplied.
    """

    if not isinstance(portrait_data, str):
        raise ValueError("The portrait must be a valid image data URL.")

    header, separator, encoded_data = portrait_data.partition(",")

    if not separator or not encoded_data:
        raise ValueError("The portrait image data is invalid.")

    allowed_headers = (
        "data:image/png;base64",
        "data:image/jpeg;base64",
        "data:image/jpg;base64",
        "data:image/webp;base64",
    )

    if header.lower() not in allowed_headers:
        raise ValueError(
            "Use a PNG, JPEG, or WebP portrait image."
        )

    try:
        image_bytes = base64.b64decode(
            encoded_data,
            validate=True
        )
    except (binascii.Error, ValueError) as exc:
        raise ValueError("The portrait image is not valid base64.") from exc

    if not image_bytes:
        raise ValueError("The portrait image is empty.")

    if len(image_bytes) > MAX_PORTRAIT_BYTES:
        raise ValueError("The portrait must be 2 MB or smaller.")

    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(
            "Pillow is required to process custom portraits. "
            "Install it in the backend Python environment."
        ) from exc

    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            image.verify()

        with Image.open(io.BytesIO(image_bytes)) as image:
            image = image.convert("RGB")
            image.save(portrait_path, format="PNG")

    except Exception as exc:
        raise ValueError(
            "The uploaded portrait could not be decoded as an image."
        ) from exc


def get_latest_generated_video(previous_files):
    """Return the newest MP4 created by this inference run."""

    current_files = [
        path
        for path in SADTALKER_RESULTS.glob("*.mp4")
        if path.is_file() and path not in previous_files
    ]

    if not current_files:
        return None

    return max(current_files, key=lambda path: path.stat().st_mtime)


# ---------------------------------------------------------
# SadTalker video generation worker
# ---------------------------------------------------------

def generate_sadtalker_video(job_id, answer, portrait_data=None):
    """
    Generate speech audio and animate a portrait with SadTalker.

    This function runs in a background thread. The frontend
    checks the job-status endpoint for progress and completion.
    """

    audio_path = SADTALKER_AUDIO / f"{job_id}.wav"
    portrait_path = SADTALKER_AUDIO / f"{job_id}_portrait.png"

    try:
        update_sadtalker_job(
            job_id,
            status="processing",
            stage="preparing",
            error=None
        )

        # Validate configuration before calling external services.
        if openai_client is None:
            raise RuntimeError("OPENAI_API_KEY is not configured.")

        if not SADTALKER_PYTHON.is_file():
            raise RuntimeError(
                f"SadTalker Python environment not found: "
                f"{SADTALKER_PYTHON}"
            )

        if not SADTALKER_INFERENCE.is_file():
            raise RuntimeError(
                f"SadTalker inference.py not found: "
                f"{SADTALKER_INFERENCE}"
            )

        if not SADTALKER_CHECKPOINTS.is_dir():
            raise RuntimeError(
                f"SadTalker checkpoints directory not found: "
                f"{SADTALKER_CHECKPOINTS}"
            )

        if portrait_data:
            validate_portrait_data(
                portrait_data,
                portrait_path
            )
            source_image = portrait_path
        else:
            source_image = DEFAULT_PORTRAIT

        if not source_image.is_file():
            raise RuntimeError(
                f"Portrait image not found: {source_image}"
            )

        # Stage 1: Generate the answer's speech.
        update_sadtalker_job(
            job_id,
            stage="generating_audio"
        )

        speech_response = openai_client.audio.speech.create(
            model="tts-1",
            voice="alloy",
            input=answer,
            response_format="wav"
        )

        speech_response.stream_to_file(str(audio_path))

        if not audio_path.is_file() or audio_path.stat().st_size == 0:
            raise RuntimeError("Speech generation returned an empty file.")

        # Stage 2: Animate the portrait.
        update_sadtalker_job(
            job_id,
            stage="generating_video"
        )

        with sadtalker_gpu_lock:
            previous_files = set(SADTALKER_RESULTS.glob("*.mp4"))

            command = [
                str(SADTALKER_PYTHON),
                str(SADTALKER_INFERENCE),
                "--driven_audio", str(audio_path),
                "--source_image", str(source_image),
                "--checkpoint_dir", str(SADTALKER_CHECKPOINTS),
                "--result_dir", str(SADTALKER_RESULTS),
                "--size", "256",
                "--preprocess", "crop",
                "--batch_size", "1",
            ]

            completed_process = subprocess.run(
                command,
                cwd=str(SADTALKER_ROOT),
                capture_output=True,
                text=True,
                timeout=SADTALKER_TIMEOUT_SECONDS,
                check=False
            )

            if completed_process.returncode != 0:
                error_output = (
                    completed_process.stderr
                    or completed_process.stdout
                    or "SadTalker exited with an unknown error."
                )

                raise RuntimeError(
                    "SadTalker inference failed:\n"
                    + error_output[-3000:]
                )

            video_path = get_latest_generated_video(previous_files)

            if video_path is None:
                raise RuntimeError(
                    "SadTalker finished without producing a new MP4 video."
                )

            if video_path.stat().st_size == 0:
                raise RuntimeError("SadTalker produced an empty video.")

        update_sadtalker_job(
            job_id,
            status="completed",
            stage="completed",
            video_url=f"/sadtalker/videos/{video_path.name}",
            error=None
        )

        logger.info(
            "SadTalker job %s completed: %s",
            job_id,
            video_path.name
        )

    except subprocess.TimeoutExpired:
        error_message = (
            "SadTalker exceeded its "
            f"{SADTALKER_TIMEOUT_SECONDS}-second timeout."
        )

        logger.error("Job %s: %s", job_id, error_message)

        update_sadtalker_job(
            job_id,
            status="failed",
            stage="failed",
            error=error_message
        )

    except Exception as exc:
        logger.exception("SadTalker job %s failed.", job_id)

        update_sadtalker_job(
            job_id,
            status="failed",
            stage="failed",
            error=str(exc)[:3000]
        )

    finally:
        # Remove temporary files, but keep generated videos.
        for temporary_path in (audio_path, portrait_path):
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "Could not remove temporary file: %s",
                    temporary_path
                )


# ---------------------------------------------------------
# Frontend routes
# ---------------------------------------------------------

@app.route("/", methods=["GET"])
def index():
    if not (FRONTEND_DIST / "index.html").is_file():
        return jsonify({
            "error": "Frontend build not found.",
            "message": (
                "Run the frontend build or start the Vite development "
                "server on port 5173."
            )
        }), 404

    return send_from_directory(str(FRONTEND_DIST), "index.html")


@app.route("/<path:path>", methods=["GET"])
def serve_static(path):
    requested_path = (FRONTEND_DIST / path).resolve()

    # Do not allow a request to escape the frontend directory.
    try:
        requested_path.relative_to(FRONTEND_DIST)
    except ValueError:
        return jsonify({"error": "Invalid path."}), 400

    if requested_path.is_file():
        return send_from_directory(
            str(FRONTEND_DIST),
            path
        )

    if (FRONTEND_DIST / "index.html").is_file():
        return send_from_directory(
            str(FRONTEND_DIST),
            "index.html"
        )

    return jsonify({"error": "Frontend file not found."}), 404


# ---------------------------------------------------------
# Health check
# ---------------------------------------------------------

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "heygen-invoice-demo",
        "sadtalker_configured": (
            SADTALKER_PYTHON.is_file()
            and SADTALKER_INFERENCE.is_file()
        ),
        "pinecone_configured": pinecone_index is not None,
        "openai_configured": openai_client is not None,
    })


# ---------------------------------------------------------
# Company guide chatbot
# ---------------------------------------------------------

@app.route("/ask", methods=["POST"])
def ask():
    data = get_json_body()
    question = data.get("question", "")

    if not isinstance(question, str) or not question.strip():
        return jsonify({"error": "No question provided."}), 400

    question = question.strip()

    try:
        context, source = get_relevant_context(question)

        if not context:
            answer = FALLBACK_ANSWER
            source = "Infylearn Guide v1.0"
        else:
            answer = generate_answer(question, context)

        return jsonify({
            "answer": answer,
            "source": source
        })

    except Exception:
        logger.exception("The /ask endpoint failed.")

        return jsonify({
            "error": "The chatbot could not process this question."
        }), 500


# ---------------------------------------------------------
# Existing HeyGen streaming API routes
# ---------------------------------------------------------

@app.route("/heygen/session", methods=["POST"])
def create_heygen_session():
    """Create a HeyGen streaming avatar session."""

    api_key = os.getenv("HEYGEN_API_KEY")
    avatar_id = os.getenv("HEYGEN_AVATAR_ID")
    voice_id = os.getenv("HEYGEN_VOICE_ID")

    if not api_key or not avatar_id or not voice_id:
        return jsonify({
            "error": "HeyGen session configuration is incomplete."
        }), 503

    try:
        resp = requests.post(
            "https://api.heygen.com/v1/streaming.new",
            json={
                "quality": "high",
                "avatar_name": avatar_id,
                "voice": {"voice_id": voice_id}
            },
            headers={
                "x-api-key": api_key,
                "Content-Type": "application/json"
            },
            timeout=30
        )

        return jsonify(resp.json()), resp.status_code

    except requests.RequestException:
        logger.exception("HeyGen session creation failed.")
        return jsonify({"error": "Could not create HeyGen session."}), 502


@app.route("/heygen/token", methods=["POST"])
def get_heygen_token():
    """Generate a HeyGen streaming token."""

    api_key = os.getenv("HEYGEN_API_KEY")

    if not api_key:
        return jsonify({"error": "HEYGEN_API_KEY is not configured."}), 503

    try:
        resp = requests.post(
            "https://api.heygen.com/v1/streaming.create_token",
            headers={"x-api-key": api_key},
            timeout=30
        )

        return jsonify(resp.json()), resp.status_code

    except requests.RequestException:
        logger.exception("HeyGen token request failed.")
        return jsonify({"error": "Could not obtain HeyGen token."}), 502


@app.route("/heygen/speak", methods=["POST"])
def heygen_speak():
    """Send text to an existing HeyGen streaming session."""

    data = get_json_body()
    session_id = data.get("session_id")
    text = data.get("text")

    if not session_id or not isinstance(text, str) or not text.strip():
        return jsonify({
            "error": "session_id and text are required."
        }), 400

    api_key = os.getenv("HEYGEN_API_KEY")

    if not api_key:
        return jsonify({"error": "HEYGEN_API_KEY is not configured."}), 503

    try:
        resp = requests.post(
            "https://api.heygen.com/v1/streaming.task",
            json={
                "session_id": session_id,
                "text": text.strip(),
                "task_type": "talk"
            },
            headers={
                "x-api-key": api_key,
                "Content-Type": "application/json"
            },
            timeout=30
        )

        return jsonify(resp.json()), resp.status_code

    except requests.RequestException:
        logger.exception("HeyGen speech request failed.")
        return jsonify({"error": "Could not send text to HeyGen."}), 502


# ---------------------------------------------------------
# Existing LiveAvatar fallback session endpoint
# ---------------------------------------------------------

@app.route("/liveavatar/start", methods=["POST"])
def start_liveavatar_session():
    """
    Create a LiveAvatar FULL mode token.

    The frontend should call this only when SadTalker fails
    and the user needs the fallback avatar.
    """

    api_key = os.getenv("LIVEAVATAR_API_KEY")
    avatar_id = os.getenv("LIVEAVATAR_AVATAR_ID")
    voice_id = os.getenv("LIVEAVATAR_VOICE_ID")
    context_id = os.getenv("LIVEAVATAR_CONTEXT_ID")

    if not api_key or not avatar_id or not voice_id:
        return jsonify({
            "error": "LiveAvatar fallback configuration is incomplete."
        }), 503

    try:
        token_resp = requests.post(
            "https://api.liveavatar.com/v1/sessions/token",
            json={
                "mode": "FULL",
                "avatar_id": avatar_id,
                "avatar_persona": {
                    "voice_id": voice_id,
                    "context_id": context_id,
                    "language": "en"
                },
                "session_duration": 240,
                "is_sandbox": True
            },
            headers={
                "X-API-KEY": api_key,
                "Content-Type": "application/json"
            },
            timeout=30
        )

        return jsonify(token_resp.json()), token_resp.status_code

    except requests.RequestException:
        logger.exception("LiveAvatar token request failed.")
        return jsonify({
            "error": "Could not start the LiveAvatar fallback."
        }), 502


# ---------------------------------------------------------
# SadTalker job API
# ---------------------------------------------------------

@app.route("/sadtalker/jobs", methods=["POST"])
def create_sadtalker_job():
    """
    Start asynchronous SadTalker video generation.

    JSON:
    {
        "answer": "Text for the avatar to speak",
        "portrait": "optional base64 image data URL"
    }
    """

    data = get_json_body()
    answer = data.get("answer", "")
    portrait_data = data.get("portrait")

    if not isinstance(answer, str) or not answer.strip():
        return jsonify({"error": "An answer is required."}), 400

    answer = answer.strip()

    if len(answer) > SADTALKER_MAX_ANSWER_LENGTH:
        return jsonify({
            "error": (
                "The answer is too long for avatar generation. "
                f"Maximum length: {SADTALKER_MAX_ANSWER_LENGTH} characters."
            )
        }), 400

    if portrait_data is not None and not isinstance(portrait_data, str):
        return jsonify({"error": "The portrait must be a data URL."}), 400

    job_id = uuid.uuid4().hex

    with sadtalker_jobs_lock:
        sadtalker_jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "stage": "queued",
            "video_url": None,
            "error": None
        }

    worker = threading.Thread(
        target=generate_sadtalker_video,
        args=(job_id, answer, portrait_data),
        daemon=True
    )

    worker.start()

    return jsonify({
        "job_id": job_id,
        "status": "queued",
        "status_url": f"/sadtalker/jobs/{job_id}"
    }), 202


@app.route("/sadtalker/jobs/<job_id>", methods=["GET"])
def get_sadtalker_job(job_id):
    """Retrieve the status of a SadTalker generation job."""

    with sadtalker_jobs_lock:
        job = sadtalker_jobs.get(job_id)

        if job is None:
            return jsonify({"error": "SadTalker job not found."}), 404

        job_copy = dict(job)

    return jsonify(job_copy)


@app.route("/sadtalker/videos/<path:filename>", methods=["GET"])
def serve_sadtalker_video(filename):
    """Serve a generated MP4 video from the results directory."""

    safe_filename = secure_filename(filename)

    if not safe_filename or safe_filename != filename:
        return jsonify({"error": "Invalid video filename."}), 400

    if Path(safe_filename).suffix.lower() != ".mp4":
        return jsonify({"error": "Only MP4 videos are allowed."}), 400

    video_path = (SADTALKER_RESULTS / safe_filename).resolve()

    try:
        video_path.relative_to(SADTALKER_RESULTS.resolve())
    except ValueError:
        return jsonify({"error": "Invalid video path."}), 400

    if not video_path.is_file():
        return jsonify({"error": "Video not found."}), 404

    response = send_file(
        str(video_path),
        mimetype="video/mp4",
        conditional=True
    )

    response.headers["Cache-Control"] = "no-store"
    return response


# ---------------------------------------------------------
# Application startup
# ---------------------------------------------------------

if __name__ == "__main__":
    print("Backend server running on http://localhost:5000")
    print("Health check: http://localhost:5000/health")
    print("SadTalker jobs: http://localhost:5000/sadtalker/jobs")
    print("SadTalker results:", SADTALKER_RESULTS)

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True,
        use_reloader=False
    )
