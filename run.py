"""Start Bid Builder:  python run.py   then open http://127.0.0.1:8000"""
import threading
import webbrowser

import uvicorn

if __name__ == "__main__":
    url = "http://127.0.0.1:8000"
    print(f"\n  CFM Bid Builder is starting at {url}  (press Ctrl+C to stop)\n")
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000)
