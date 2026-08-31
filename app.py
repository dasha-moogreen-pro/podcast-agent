"""Personal podcast brief agent. One public episode URL -> a personalized Russian brief."""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import requests
from flask import Flask, jsonify, render_template, request
from openai import OpenAI
from yt_dlp import YoutubeDL

app = Flask(__name__)

SUMMARY_PROMPT = """Ты — персональный агент Даши по смысловой обработке подкастов и интервью.
Не пересказывай выпуск. Отфильтруй его через её реальные цели и стиль мышления.

Контекст: Даша развивает MOOGREEN и сейчас особенно нуждается в практических решениях
для вывода MOOGREEN PRO в реальные B2B-продажи. Ей важны системное мышление, причинные
связи, прозрачное разделение факта, вывода и гипотезы. Она не любит банальности,
самопрезентацию и советы без границ применимости. Приоритет: бизнес, затем саморазвитие,
затем здоровье. Цель — рост прибыли, более быстрые качественные решения, предотвращение
дорогих ошибок и меньшая операционная нагрузка на собственника.

Пиши по-русски, компактно и удобно для первого экрана телефона. Используй ровно этот формат:

[Название / гость]

Главное за 30 секунд
— один наиболее ценный вывод именно для Даши.

Стоит забрать
1. … — почему это важно именно ей.
2. … — почему это важно именно ей.
3. … — почему это важно именно ей.

Применение
— до 3 конкретных действий или решений. Если действий нет: «сохраняем как мыслительную модель, действий сейчас не требуется».

Проверка на прочность
— Факт: только проверяемое или прямо сказанное в выпуске, с указанием, если это утверждение гостя.
— Обоснованная интерпретация: …
— Спорное / требует проверки: …

Детали и таймкоды
— только важные фрагменты. Указывай таймкоды лишь если они есть во входном тексте; не выдумывай.

Вердикт: [обязательно / выборочно / можно пропустить].
"""


def title_from_url(url: str) -> str:
    host = urlparse(url).netloc.removeprefix("www.")
    return f"Выпуск с {host}"


def download_audio(url: str, directory: Path) -> Path:
    """Use yt-dlp for YouTube and podcast pages; direct audio URLs get a safe fallback."""
    output = str(directory / "source.%(ext)s")
    options = {
        "outtmpl": output,
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 60,
    }
    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
            requested = info.get("requested_downloads", [])
            if requested and requested[0].get("filepath"):
                return Path(requested[0]["filepath"])
    except Exception as extraction_error:
        # Some podcast hosts expose a direct MP3 link; downloading it is a valid fallback.
        if re.search(r"\.(mp3|m4a|wav|webm)(?:$|[?&])", url, re.I):
            target = directory / "source.mp3"
            response = requests.get(url, timeout=120, stream=True)
            response.raise_for_status()
            with target.open("wb") as stream:
                for chunk in response.iter_content(1024 * 1024):
                    stream.write(chunk)
            return target
        raise RuntimeError(
            "Не удалось получить аудио по этой ссылке. Пришли прямую ссылку на выпуск "
            "или попробуй зеркало (YouTube/Spotify)."
        ) from extraction_error
    files = list(directory.glob("source.*"))
    if not files:
        raise RuntimeError("Площадка не отдала аудиофайл.")
    return files[0]


def transcribe(audio_path: Path) -> str:
    client = OpenAI()
    with audio_path.open("rb") as audio:
        result = client.audio.transcriptions.create(
            model="gpt-transcribe", file=audio, language="ru",
            prompt="Русский подкаст. Важные имена и термины: Даша, MOOGREEN, MOOGREEN PRO, ВкусВилл, Ozon, Wildberries, B2B."
        )
    return result.text


def make_brief(transcript: str, title: str) -> str:
    client = OpenAI()
    response = client.responses.create(
        model=os.environ.get("SUMMARY_MODEL", "gpt-5-mini"),
        instructions=SUMMARY_PROMPT,
        input=f"Название выпуска: {title}\n\nРасшифровка:\n{transcript}",
    )
    return response.output_text


@app.get("/")
def home():
    return render_template("index.html")


@app.post("/api/brief")
def brief():
    url = (request.json or {}).get("url", "").strip()
    if not url.startswith(("https://", "http://")):
        return jsonify(error="Вставь полную публичную ссылку на эпизод."), 400
    if not os.environ.get("OPENAI_API_KEY"):
        return jsonify(error="В сервисе ещё не задан OPENAI_API_KEY."), 500
    try:
        with tempfile.TemporaryDirectory() as temp:
            audio = download_audio(url, Path(temp))
            transcript = transcribe(audio)
            result = make_brief(transcript, title_from_url(url))
        return jsonify(summary=result)
    except Exception as error:  # return a readable operational error, never a stack trace
        return jsonify(error=str(error)), 502


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
