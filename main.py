# main.py - iframe-aware async proxy
import os, time, traceback, asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# Root page (login wrapper) and fallback app URL
ROOT_URL = "https://bharatvision.streamlit.app"
# Env secrets (set in Railway)
BV_USER = os.getenv("BV_USER")
BV_PASS = os.getenv("BV_PASS")
HEADLESS = os.getenv("HEADLESS", "true").lower() != "false"

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Selectors inside iframe/app
USERNAME_SEL = 'input[placeholder="Enter username"]'
PASSWORD_SEL = 'input[placeholder="Enter password"]'
LOGIN_BTN_SEL = 'button:has-text("Login")'
TEXTAREA_CANDIDATES = [
    "textarea",
    "div[data-testid='stTextArea'] textarea",
]
BUTTON_CANDIDATES = [
    "button[data-testid='stButton']",
    "button:has-text('Analyze')",
    "button"
]
OUTPUT_CANDIDATES = [
    "div.stMarkdown",
    "div[data-testid='stMarkdown']",
    "pre",
]

async def ensure_in_iframe_context(page, debug):
    """If page contains an iframe, extract its src and navigate into it."""
    try:
        # Try to find an iframe element
        iframe_el = await page.query_selector("iframe")
        if not iframe_el:
            debug.append("no iframe found on root page")
            return page  # stay on same page

        src = await iframe_el.get_attribute("src")
        debug.append(f"found iframe src raw: {repr(src)}")
        if not src:
            debug.append("iframe src empty -> staying on parent page")
            return page

        # Normalize src into full URL
        src = src.strip()
        if src.startswith("//"):
            src = "https:" + src
        elif src.startswith("/"):
            # relative path on same host
            src = ROOT_URL.rstrip("/") + src
        elif not src.startswith("http"):
            # fallback: assume same host
            src = ROOT_URL.rstrip("/") + "/" + src

        debug.append(f"navigating into iframe URL: {src}")

        # Create a new page/tab and navigate to iframe URL directly to avoid sandbox issues
        browser = page.context.browser
        new_page = await browser.new_page()
        await new_page.goto(src, wait_until="networkidle")
        await asyncio.sleep(1.5)
        debug.append(f"in-iframe page url after goto: {new_page.url}")
        return new_page
    except Exception as e:
        debug.append(f"iframe handling error: {repr(e)}")
        return page

async def try_login_if_present(page, debug):
    """Try to login on the current page/frame if username/password inputs exist."""
    try:
        # Check presence of username/password inputs inside this page/frame
        user_exists = await page.query_selector(USERNAME_SEL)
        pass_exists = await page.query_selector(PASSWORD_SEL)
        if user_exists and pass_exists and BV_USER and BV_PASS:
            debug.append("login inputs detected inside iframe/page -> attempting login")
            try:
                await page.fill(USERNAME_SEL, BV_USER)
                debug.append("filled username in iframe")
            except Exception as e:
                debug.append(f"username fill error: {repr(e)}")
            try:
                await page.fill(PASSWORD_SEL, BV_PASS)
                debug.append("filled password in iframe")
            except Exception as e:
                debug.append(f"password fill error: {repr(e)}")
            try:
                await page.click(LOGIN_BTN_SEL)
                debug.append("clicked login button in iframe")
            except Exception as e:
                debug.append(f"login click error in iframe: {repr(e)}")
            # wait for login redirect / UI update
            await asyncio.sleep(3.0)
            debug.append(f"url after iframe login attempt: {page.url}")
            return True
        else:
            debug.append("no login inputs detected inside iframe/page (or BV_USER/BV_PASS missing)")
            return False
    except Exception as e:
        debug.append(f"login check error: {repr(e)}")
        return False

async def fill_and_click_app(page, text, debug):
    filled = False
    clicked = False
    # Try textarea selectors
    for sel in TEXTAREA_CANDIDATES:
        try:
            el = await page.query_selector(sel)
            if el:
                await el.fill(text)
                debug.append(f"filled using selector: {sel}")
                filled = True
                break
        except Exception as e:
            debug.append(f"fill error {sel}: {repr(e)}")
    # Try clicking app button(s)
    for sel in BUTTON_CANDIDATES:
        try:
            btn = await page.query_selector(sel)
            if btn:
                await btn.click()
                debug.append(f"clicked button using selector: {sel}")
                clicked = True
                break
        except Exception as e:
            debug.append(f"button click error {sel}: {repr(e)}")
    return filled, clicked

async def extract_output(page, debug):
    texts = []
    for sel in OUTPUT_CANDIDATES:
        try:
            nodes = await page.query_selector_all(sel)
            for n in nodes:
                try:
                    t = (await n.inner_text()).strip()
                    if t and t not in texts:
                        texts.append(t)
                        debug.append(f"extracted using {sel}")
                except Exception as e:
                    debug.append(f"inner_text error {sel}: {repr(e)}")
        except Exception as e:
            debug.append(f"query_all error {sel}: {repr(e)}")
    if not texts:
        try:
            body = (await page.inner_text("body"))[:120000]
            texts.append(body)
            debug.append("body fallback extracted")
        except Exception as e:
            debug.append(f"body fallback error: {repr(e)}")
    return "\n\n---\n\n".join(texts)

async def run_proxy(text, timeout_s=45):
    debug = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=HEADLESS, args=["--no-sandbox"])
            context = await browser.new_context()
            page = await context.new_page()
            page.set_default_timeout(timeout_s * 1000)

            debug.append("navigating to root/login wrapper")
            await page.goto(ROOT_URL, wait_until="networkidle")
            await asyncio.sleep(1.2)
            # if iframe present, go inside it (returns a page object we can operate on)
            inner_page = await ensure_in_iframe_context(page, debug)

            # try login inside inner_page if applicable
            login_done = await try_login_if_present(inner_page, debug)
            if login_done:
                # after login, sometimes the app reloads in same frame; wait and maybe navigate again
                await asyncio.sleep(2.0)

            # ensure we are on the app URL (some apps use same origin root + iframe)
            # If inner_page is different from page (we opened new_page), use it; else ensure navigate to ROOT_URL
            if inner_page != page:
                app_page = inner_page
            else:
                app_page = page
                await app_page.goto(ROOT_URL, wait_until="networkidle")
                await asyncio.sleep(1.2)

            debug.append(f"current page url before app actions: {app_page.url}")

            # Try to fill and click the app controls
            filled, clicked = await fill_and_click_app(app_page, text, debug)
            debug.append(f"filled={filled}, clicked={clicked}")

            # Wait and extract output
            tstart = time.time()
            output = ""
            while time.time() - tstart < timeout_s:
                output = await extract_output(app_page, debug)
                if output and len(output) > 10:
                    debug.append("sufficient output found")
                    break
                await asyncio.sleep(1.0)

            snapshot = ""
            try:
                snapshot = (await app_page.content())[:120000]
            except Exception as e:
                debug.append(f"snapshot error: {repr(e)}")

            await browser.close()
            return {"result": output, "debug": debug, "page_url_after": app_page.url, "snapshot_snippet": snapshot}
    except Exception as e:
        return {"error": "proxy_exception", "trace": traceback.format_exc(), "debug": debug}

app.post = app.post  # avoid linter noise

@app.post("/validate")
async def validate(payload: dict):
    text = payload.get("text", "")
    if not text:
        return {"error": "No text provided"}
    return await run_proxy(text)
