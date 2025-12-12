from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
import traceback
import time
import asyncio

STREAMLIT_URL = "https://bharatvision.streamlit.app"

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

INPUT_SELECTORS = [
    "textarea",
    "div[data-testid='stTextArea'] textarea",
]

BUTTON_SELECTORS = [
    "button[data-testid='stButton']",
    "button",
]

OUTPUT_SELECTORS = [
    "div.stMarkdown",
    "div[data-testid='stMarkdown']",
    "pre",
]

async def run_streamlit_async(text: str, timeout_s: int = 40):
    debug = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
            page = await browser.new_page()
            page.set_default_timeout(timeout_s * 1000)

            debug.append("navigating to streamlit app")
            await page.goto(STREAMLIT_URL, wait_until="networkidle")
            await asyncio.sleep(1.5)

            # fill input
            for sel in INPUT_SELECTORS:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        await el.fill(text)
                        debug.append(f"filled using {sel}")
                        break
                except Exception as e:
                    debug.append(f"fill error {sel}: {repr(e)}")

            # click button
            for sel in BUTTON_SELECTORS:
                try:
                    btn = await page.query_selector(sel)
                    if btn:
                        await btn.click()
                        debug.append(f"clicked button {sel}")
                        break
                except Exception as e:
                    debug.append(f"button error {sel}: {repr(e)}")

            # wait for output
            output_text = ""
            tstart = time.time()
            while time.time() - tstart < timeout_s:
                for sel in OUTPUT_SELECTORS:
                    try:
                        nodes = await page.query_selector_all(sel)
                        for n in nodes:
                            t = await n.inner_text()
                            if t.strip():
                                output_text = t
                                debug.append(f"output found via {sel}")
                                break
                        if output_text:
                            break
                    except:
                        pass
                if output_text:
                    break
                await asyncio.sleep(1)

            await browser.close()
            return {"result": output_text, "debug": debug}

    except Exception as e:
        return {"error": "proxy_error", "trace": traceback.format_exc(), "debug": debug}

@app.post("/validate")
async def validate(payload: dict):
    text = payload.get("text", "")
    return await run_streamlit_async(text)
