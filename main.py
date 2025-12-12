from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from playwright.sync_api import sync_playwright

STREAMLIT_URL = "https://bharatvision.streamlit.app"

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def run_streamlit_lmpc(text: str):
    """Automates Streamlit UI without modifying your code."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # Load your Streamlit page
        page.goto(STREAMLIT_URL, timeout=120000)

        # Wait for Streamlit to load
        page.wait_for_timeout(3000)

        # Type into your input box (modify selector if needed)
        page.fill("textarea", text)

        # Press the analyze/submit button
        page.click("button")

        # Wait for result to load
        page.wait_for_timeout(5000)

        # Extract full result text from Streamlit output area
        result = page.inner_text("div.stMarkdown")

        browser.close()
        return result


@app.post("/validate")
async def validate(request_data: dict):
    text = request_data.get("text", "")
    
    try:
        output = run_streamlit_lmpc(text)
        return {"result": output}
    except Exception as e:
        return {"error": str(e)}
