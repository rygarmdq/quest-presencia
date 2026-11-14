#!/usr/bin/env python3
# PRESENCIA v1.2 - Puente de IA local para Meta Quest.
# Requiere Python 3.9+ y Ollama con qwen2.5:1.5b. Sin librerías Python adicionales.
# IMPORTANTE: el servidor SOLO escucha en 127.0.0.1:8765.
import hmac
import json
import secrets
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "127.0.0.1"
PORT = 8765
MODEL = "qwen2.5:1.5b"
OLLAMA = "http://127.0.0.1:11434"
ALLOWED_ORIGIN = "https://rygarmdq.github.io"
SECRET = secrets.token_urlsafe(15)  # 20 caracteres; cambia cada ejecución.
SYSTEM = (
    "Sos PRESENCIA, un asistente virtual local de prueba en realidad mixta. "
    "Respondé en español natural, claro, cálido y conciso, idealmente en 1 a 3 frases. "
    "Usá voseo argentino cuando corresponda. "
    "Si no sabés algo, decilo; nunca inventes fuentes, resultados ni capacidades. "
    "No sos ChatGPT y no tenés conexión a Internet ni acceso a datos personales. "
    "No afirmes poder ejecutar tareas en apps o dispositivos. "
    "No incluyas Markdown, tablas, enlaces ni emojis: tu respuesta se leerá en voz alta."
)

def ollama_call(endpoint, payload=None, timeout=8):
    if payload is None:
        request = urllib.request.Request(OLLAMA + endpoint, method="GET")
    else:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            OLLAMA + endpoint, data=data,
            headers={"Content-Type": "application/json"}, method="POST"
        )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read(262144).decode("utf-8"))

class PresenceHandler(BaseHTTPRequestHandler):
    server_version = "PresenciaPuente/1.2"

    def log_message(self, *args):
        # No guardar conversaciones ni claves en los registros.
        return

    def send_json(self, status, data):
        encoded = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        if self.headers.get("Origin") == ALLOWED_ORIGIN:
            self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
            self.send_header("Vary", "Origin")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_OPTIONS(self):
        if self.headers.get("Origin") != ALLOWED_ORIGIN:
            self.send_json(403, {"error": "Origen no autorizado"})
            return
        if self.path not in ("/health", "/chat"):
            self.send_json(404, {"error": "Ruta desconocida"})
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Presencia-Key")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Vary", "Origin")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self.send_json(404, {"error": "Usar POST /health o POST /chat"})

    def do_POST(self):
        if self.path not in ("/health", "/chat"):
            self.send_json(404, {"error": "Ruta desconocida"})
            return
        if self.headers.get("Origin") != ALLOWED_ORIGIN:
            self.send_json(403, {"error": "Origen no autorizado"})
            return
        incoming = self.headers.get("X-Presencia-Key", "")
        if not incoming or not hmac.compare_digest(incoming, SECRET):
            self.send_json(401, {"error": "Clave de conexión incorrecta"})
            return
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_json(400, {"error": "Longitud inválida"})
            return
        if n < 2 or n > 8192:
            self.send_json(413, {"error": "Mensaje demasiado grande"})
            return
        try:
            data = json.loads(self.rfile.read(n).decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("JSON debe ser un objeto")
        except (ValueError, UnicodeDecodeError):
            self.send_json(400, {"error": "JSON incorrecto"})
            return

        if self.path == "/health":
            try:
                tags = ollama_call("/api/tags", timeout=5)
                available = [m.get("name", "") for m in tags.get("models", [])]
                if MODEL not in available and not any(x.startswith(MODEL + "-") for x in available):
                    self.send_json(503, {
                        "error": "No encuentro el modelo " + MODEL,
                        "hint": "En la PC ejecutá: ollama pull " + MODEL
                    })
                else:
                    self.send_json(200, {"ok": True, "model": MODEL})
            except Exception:
                self.send_json(503, {"error": "No puedo comunicarme con Ollama en la PC"})
            return

        question = data.get("text", "")
        if not isinstance(question, str) or len(question.strip()) < 1 or len(question) > 380:
            self.send_json(400, {"error": "La pregunta debe tener hasta 380 caracteres"})
            return
        history = data.get("history", [])
        if not isinstance(history, list):
            self.send_json(400, {"error": "Historial inválido"})
            return
        messages = [{"role": "system", "content": SYSTEM}]
        for item in history[-8:]:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            content = item.get("content")
            if role in ("user", "assistant") and isinstance(content, str):
                messages.append({"role": role, "content": content[:380]})
        messages.append({"role": "user", "content": question.strip()})
        try:
            response = ollama_call("/api/chat", {
                "model": MODEL,
                "stream": False,
                "messages": messages,
                "options": {"temperature": 0.45, "num_predict": 135},
                "keep_alive": "10m"
            }, timeout=85)
            answer = response.get("message", {}).get("content", "").strip()
            if not answer:
                self.send_json(502, {"error": "La IA devolvió una respuesta vacía"})
                return
            self.send_json(200, {"reply": answer[:1050], "model": MODEL})
        except urllib.error.HTTPError as error:
            self.send_json(502, {"error": "Ollama rechazó la consulta (HTTP " + str(error.code) + ")"})
        except Exception:
            self.send_json(503, {"error": "Ollama no respondió. Revisá que esté abierto y el modelo descargado."})

if __name__ == "__main__":
    try:
        httpd = ThreadingHTTPServer((HOST, PORT), PresenceHandler)
    except OSError as error:
        print("No se pudo iniciar el puente:", str(error), file=sys.stderr)
        sys.exit(1)
    print("\n=============================================")
    print("  PRESENCIA v1.2 - PUENTE LOCAL DE IA")
    print("=============================================")
    print("Servidor: http://127.0.0.1:" + str(PORT))
    print("Modelo:", MODEL)
    print("CLAVE SECRETA para ingresar en PRESENCIA:")
    print("\n    " + SECRET + "\n")
    print("No publiques esta clave y no compartas esta consola.")
    print("Abrí otra terminal y ejecutá:")
    print("cloudflared tunnel --url http://localhost:" + str(PORT))
    print("Copiá la URL HTTPS generada al formulario de PRESENCIA v1.2.")
    print("Cerrá esta ventana para apagar el puente.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
