import os
import time
import subprocess
import json
import pyautogui
import threading
import base64
import customtkinter as ctk
from PIL import Image

# Import models
import google.generativeai as genai
import anthropic
import dashscope
from dashscope import MultiModalConversation

PROMPT = """
You are AIcceptor, a security agent monitoring an AI coding assistant named 'Antigravity'.
Your job is to look at the provided screenshot of the user's IDE/screen and determine if Antigravity is asking the user to accept a code change, run a terminal command, or allow an action.

Analyze the screenshot carefully and decide if the proposed action is SAFE or UNSAFE based on these absolute rules:

**SAFE ACTIONS:**
- Edits made to source code files within the current project.
- Reading files within the current project.
- Running standard build or test commands (e.g., `npm run dev`, `npm test`, `pytest`, `cargo build`).
- Standard file creation/deletion *within* the project directory.
- Standard git commands on the current repository.

**UNSAFE ACTIONS (Require User Review):**
- System-level commands (e.g., `rm -rf /`, formatting disks, changing system configs).
- Modifying files outside the current project structure (e.g., editing `~/.bashrc`, changing global settings).
- Installing global dependencies that look suspicious.
- Hallucinations (making up non-existent files or directories).
- Any action you cannot confidently determine is safe.

Analyze the screen. Determine the status.
If you find a prompt from Antigravity asking to "Accept" or "Allow", and you determine it is SAFE, you MUST provide the exact X and Y coordinates of the center of that "Accept" or "Allow" button so I can click it.

Respond strictly in the following JSON format:
{
  "status": "SAFE" | "UNSAFE" | "NONE",
  "reason": "Brief explanation of your decision (e.g., 'Modifying standard project file index.js', 'Attempting to run unsafe command sudo rm -rf')",
  "button_coordinates": {
    "x": <integer or null>,
    "y": <integer or null>
  }
}

Return ONLY valid JSON.
"""

def take_screenshot(filename="/tmp/aicceptor_screen.png"):
    """Takes a screenshot using native macOS utility."""
    # -x mutes the sound, -C includes the cursor
    subprocess.run(["screencapture", "-x", "-C", filename], check=True)
    return filename

def notify_user(message, title="AIcceptor Alert"):
    """Sends a native macOS notification."""
    script = f'display notification "{message}" with title "{title}" sound name "Basso"'
    subprocess.run(["osascript", "-e", script])

def encode_image_base64(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

def call_gemini(image_path, api_key):
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-1.5-pro')
    img = Image.open(image_path)
    response = model.generate_content([PROMPT, img])
    return response.text.strip()

def call_claude(image_path, api_key):
    client = anthropic.Anthropic(api_key=api_key)
    base64_image = encode_image_base64(image_path)
    message = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": base64_image,
                        },
                    },
                    {"type": "text", "text": PROMPT}
                ],
            }
        ],
    )
    return message.content[0].text

def call_qwen(image_path, api_key):
    dashscope.api_key = api_key
    messages = [
        {
            "role": "user",
            "content": [
                {"image": f"file://{image_path}"},
                {"text": PROMPT}
            ]
        }
    ]
    response = MultiModalConversation.call(model='qwen-vl-max', messages=messages)
    if response.status_code == 200:
        return response.output.choices[0].message.content[0].get('text', '')
    else:
        raise Exception(f"Qwen error: {response.code} {response.message}")

class AIcceptorApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("AIcceptor")
        self.geometry("450x450")
        self.resizable(False, False)
        
        # State
        self.running = False
        self.monitor_thread = None
        self.last_action_time = 0
        
        # UI Elements
        self.title_label = ctk.CTkLabel(self, text="AIcceptor", font=ctk.CTkFont(size=24, weight="bold"))
        self.title_label.pack(pady=(20, 10))
        
        # Model Selection
        self.model_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.model_frame.pack(fill="x", padx=20, pady=5)
        self.model_label = ctk.CTkLabel(self.model_frame, text="Select Model:")
        self.model_label.pack(side="left")
        self.model_var = ctk.StringVar(value="Gemini 1.5 Pro")
        self.model_dropdown = ctk.CTkOptionMenu(self.model_frame, variable=self.model_var, 
                                                values=["Gemini 1.5 Pro", "Claude 3.5 Sonnet", "Qwen VL Max"])
        self.model_dropdown.pack(side="right", fill="x", expand=True, padx=(10, 0))
        
        # API Key
        self.api_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.api_frame.pack(fill="x", padx=20, pady=5)
        self.api_label = ctk.CTkLabel(self.api_frame, text="API Key:")
        self.api_label.pack(side="left")
        self.api_entry = ctk.CTkEntry(self.api_frame, show="*", placeholder_text="sk-...")
        self.api_entry.pack(side="right", fill="x", expand=True, padx=(10, 0))
        
        # Try load from .env if present
        try:
            from dotenv import load_dotenv
            load_dotenv()
            if os.getenv("GEMINI_API_KEY"):
                self.api_entry.insert(0, os.getenv("GEMINI_API_KEY"))
        except:
            pass

        # Interval
        self.interval_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.interval_frame.pack(fill="x", padx=20, pady=5)
        self.interval_label = ctk.CTkLabel(self.interval_frame, text="Interval (sec):")
        self.interval_label.pack(side="left")
        self.interval_entry = ctk.CTkEntry(self.interval_frame, placeholder_text="30")
        self.interval_entry.insert(0, "30")
        self.interval_entry.pack(side="right", fill="x", expand=True, padx=(10, 0))
        
        # Buttons
        self.button_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.button_frame.pack(fill="x", padx=20, pady=20)
        self.start_btn = ctk.CTkButton(self.button_frame, text="Start", fg_color="green", hover_color="darkgreen", command=self.start_monitoring)
        self.start_btn.pack(side="left", expand=True, padx=5)
        self.stop_btn = ctk.CTkButton(self.button_frame, text="Stop", fg_color="red", hover_color="darkred", state="disabled", command=self.stop_monitoring)
        self.stop_btn.pack(side="right", expand=True, padx=5)

        # Status
        self.log_textbox = ctk.CTkTextbox(self, height=120)
        self.log_textbox.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        self.log_textbox.insert("end", "Welcome to AIcceptor.\nReady to start.\n")
        self.log_textbox.configure(state="disabled")

    def log(self, message):
        self.log_textbox.configure(state="normal")
        self.log_textbox.insert("end", f"> {message}\n")
        self.log_textbox.see("end")
        self.log_textbox.configure(state="disabled")

    def start_monitoring(self):
        api_key = self.api_entry.get().strip()
        if not api_key:
            self.log("Error: API Key is required.")
            return
            
        try:
            interval = int(self.interval_entry.get().strip())
        except ValueError:
            self.log("Error: Interval must be an integer.")
            return

        self.running = True
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.model_dropdown.configure(state="disabled")
        self.api_entry.configure(state="disabled")
        self.interval_entry.configure(state="disabled")
        
        self.log("Starting monitoring in background...")
        self.monitor_thread = threading.Thread(target=self.run_loop, args=(self.model_var.get(), api_key, interval), daemon=True)
        self.monitor_thread.start()

    def stop_monitoring(self):
        self.running = False
        self.log("Stopping... please wait for current cycle to finish.")

    def run_loop(self, model_name, api_key, interval):
        cooldown = 10 # Seconds to wait after an action before re-evaluating
        
        while self.running:
            current_time = time.time()
            if current_time - self.last_action_time < cooldown:
                time.sleep(2)
                continue
                
            self.log(f"Taking screenshot... using {model_name}")
            screenshot_path = take_screenshot()
            
            try:
                if model_name == "Gemini 1.5 Pro":
                    text = call_gemini(screenshot_path, api_key)
                elif model_name == "Claude 3.5 Sonnet":
                    text = call_claude(screenshot_path, api_key)
                elif model_name == "Qwen VL Max":
                    text = call_qwen(screenshot_path, api_key)
                else:
                    raise Exception("Unknown model selected.")
                
                # Clean up JSON
                if text.startswith("```json"):
                    text = text[7:]
                elif text.startswith("```"):
                    text = text[3:]
                if text.endswith("```"):
                    text = text[:-3]
                    
                result = json.loads(text.strip())
                
                status = result.get("status")
                
                if status == "SAFE":
                    coords = result.get("button_coordinates")
                    if coords and coords.get("x") is not None and coords.get("y") is not None:
                        x = coords["x"]
                        y = coords["y"]
                        self.log(f"SAFE detected. Clicking at ({x}, {y}).")
                        
                        # Temporarily hijack mouse
                        original_x, original_y = pyautogui.position()
                        pyautogui.moveTo(x, y, duration=0.2)
                        pyautogui.click()
                        pyautogui.moveTo(original_x, original_y, duration=0.1)
                        
                        self.last_action_time = time.time()
                    else:
                        self.log("SAFE action, but no coordinates provided.")
                
                elif status == "UNSAFE":
                    reason = result.get("reason", "Unknown Reason")
                    self.log(f"UNSAFE ACTION DETECTED.")
                    notify_user(message=f"Review needed: {reason}", title="⚠️ AIcceptor Alert")
                    self.last_action_time = time.time()
                
                elif status == "NONE":
                    self.log("No Antigravity prompt detected.")
                    
            except Exception as e:
                self.log(f"API Error: {str(e)}")
            
            # Clean up
            if os.path.exists(screenshot_path):
                os.remove(screenshot_path)
            
            # Sleep in small chunks so we can interrupt quickly if user clicks "Stop"
            for _ in range(interval):
                if not self.running:
                    break
                time.sleep(1)
        
        self.log("Stopped monitoring.")
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.model_dropdown.configure(state="normal")
        self.api_entry.configure(state="normal")
        self.interval_entry.configure(state="normal")

if __name__ == "__main__":
    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme("blue")
    
    app = AIcceptorApp()
    app.mainloop()
