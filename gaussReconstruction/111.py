#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scraper_api.py — 附件归档服务（最终版）
策略摘要：
1) 策略A（首选）:
   - 点击页面上的 opendown() 触发 layer.open iframe（附件弹窗）
   - 切入该 iframe，查找 <a> 标签：
       - 若 href 为真实 http 链接，直接下载（requests，继承浏览器 cookies）
       - 若 onclick 包含 GetValidateCode('selid')，点击后：
           a) 等待新窗口（window.open）并抓取其 URL（若为文件则下载）
           b) 使用 Chrome performance logs + CDP(Network.getResponseBody) 解析 XHR 响应体，寻找 json.result.urlhref 并下载
           c) 检查 iframe 中 id=selid 的容器是否被填充出真实链接并下载
2) 策略B（兜底）:
   - 在主页面扫描所有 <a> 的 href 和页面源码中的 href="..."，寻找后缀为 .pdf/.doc/.zip/.rar 等的链接并下载
3) 下载后：
   - 若是 zip/rar 解压后逐项上传至毕昇知识库（KNOWLEDGE_BASE_ID）
   - 非压缩文件按命名规则上传
4) 日志：详细 messages 返回并写入 mission_log.txt
"""
import os
import re
import json
import time
import base64
import shutil
import logging
import tempfile
import traceback
from pathlib import Path
from typing import List, Tuple, Optional
from urllib.parse import urljoin, urlparse, unquote

import requests
from flask import Flask, request, jsonify

# Selenium + exceptions
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException, UnexpectedAlertPresentException

# Archive
import zipfile
try:
    import patoolib
except Exception:
    patoolib = None

# -------------------------
# CONFIG (请确认/修改)
# -------------------------
BISHENG_API_BASE_URL = "http://192.168.168.19:3001"
KNOWLEDGE_BASE_ID = 254

SERVER_BIND_IP = "0.0.0.0"
SERVER_PORT = 5000

BASE_DIR = Path("/root/scraper_project").resolve()
TEMP_DOWNLOAD_DIR = BASE_DIR / "temp_downloads"
MISSION_LOG_FILE = BASE_DIR / "mission_log.txt"

DOWNLOAD_WORK_DIR = TEMP_DOWNLOAD_DIR  # 临时下载并处理目录
os.makedirs(TEMP_DOWNLOAD_DIR, exist_ok=True)

# Chrome / chromedriver
CHROMEDRIVER_PATH = os.environ.get("CHROMEDRIVER_PATH", "/usr/local/bin/chromedriver")
CHROME_BINARY = os.environ.get("CHROME_BINARY", "/usr/bin/google-chrome-stable")

HEADLESS = True
REQUESTS_VERIFY = False  # 内网证书容错，必要时改为 True

# file ext that we consider attachments
ATTACH_EXTS = ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.zip', '.rar', '.txt']

# timeouts
IFRAME_WAIT_SEC = 6
CLICK_NEW_WINDOW_WAIT = 8
PERF_POLL_ITER = 6  # poll ×sleep(0.8) total ~5s
PERF_POLL_SLEEP = 0.8

# -------------------------
# Logging
# -------------------------
os.makedirs(BASE_DIR, exist_ok=True)
handler = logging.FileHandler(MISSION_LOG_FILE, encoding="utf-8")
formatter = logging.Formatter("%(asctime)s -[%(levelname)s]- (PID:%(process)d) - %(message)s")
handler.setFormatter(formatter)
logger = logging.getLogger("scraper_api")
logger.setLevel(logging.INFO)
if not logger.handlers:
    logger.addHandler(handler)
    import sys
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(formatter)
    logger.addHandler(sh)

# -------------------------
# Flask
# -------------------------
app = Flask(__name__)

# -------------------------
# Helper utilities
# -------------------------
def sanitize_filename(name: str) -> str:
    if not name:
        return ""
    s = re.sub(r'[\\/:*?"<>|\r\n]+', '_', name)
    s = re.sub(r'\s+', '_', s)
    return s.strip()[:200]

def short_title(text: str) -> str:
    return sanitize_filename(text)[:15]

def derive_filename_from_url(url: str) -> str:
    p = urlparse(url)
    name = unquote(Path(p.path).name or p.fragment or 'download')
    return sanitize_filename(name)

def looks_like_attachment(s: Optional[str]) -> bool:
    if not s:
        return False
    s = s.lower()
    return any(s.endswith(ext) or (ext in s and s.endswith(tuple(ATTACH_EXTS))) for ext in ATTACH_EXTS)

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

# -------------------------
# Selenium driver builder (enable Network domain)
# -------------------------
def build_driver(download_dir: Path, headless: bool = True):
    options = Options()
    if headless:
        # compatible headless
        try:
            options.add_argument("--headless=new")
        except Exception:
            options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-popup-blocking")
    options.add_argument(f"--user-data-dir={tempfile.mkdtemp(prefix='chrome_profile_')}")
    options.add_argument("--window-size=1366,900")
    if CHROME_BINARY:
        options.binary_location = CHROME_BINARY

    prefs = {
        "download.default_directory": str(download_dir),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "plugins.always_open_pdf_externally": True  # PDF 使用外部下载
    }
    options.add_experimental_option("prefs", prefs)
    # performance logs
    options.set_capability('goog:loggingPrefs', {'performance': 'ALL', 'browser': 'ALL'})

    # create driver
    try:
        if CHROMEDRIVER_PATH:
            driver = webdriver.Chrome(options=options, executable_path=CHROMEDRIVER_PATH)
        else:
            driver = webdriver.Chrome(options=options)
    except TypeError:
        # some selenium versions use different signature
        driver = webdriver.Chrome(options=options)
    # enable Network domain for CDP response body retrieval
    try:
        driver.execute_cdp_cmd("Network.enable", {})
    except Exception:
        # not fatal; we still try other fallbacks
        logger.info("CDP Network.enable not available in this environment.")
    driver.set_page_load_timeout(60)
    driver.set_script_timeout(60)
    return driver

# -------------------------
# session from driver (cookies)
# -------------------------
def session_from_driver(driver) -> requests.Session:
    s = requests.Session()
    s.verify = REQUESTS_VERIFY
    try:
        for c in driver.get_cookies():
            # c may have domain starting with dot
            s.cookies.set(c['name'], c['value'], domain=c.get('domain'))
    except Exception:
        pass
    s.headers.update({'User-Agent': 'Mozilla/5.0 (AttachmentBot/1.0)'})
    return s

# -------------------------
# Performance logs parse + CDP response-body inspection
# -------------------------
def extract_urls_from_perf_logs(driver) -> List[str]:
    urls = []
    try:
        logs = driver.get_log('performance')
    except Exception:
        return urls
    seen = set()
    for entry in logs:
        try:
            msg = json.loads(entry.get('message', '{}')).get('message', {})
            method = msg.get('method', '')
            if method == 'Network.responseReceived':
                params = msg.get('params', {})
                resp = params.get('response', {})
                request_id = params.get('requestId')
                # first try headers / url
                url = resp.get('url', '')
                mime = resp.get('mimeType', '') or ''
                headers = resp.get('headers') or {}
                cd = (headers.get('Content-Disposition') or headers.get('content-disposition') or '')
                if url and (looks_like_attachment(url) or 'attachment' in cd.lower() or any(k in mime for k in ['pdf', 'zip', 'rar', 'msword'])):
                    if url not in seen:
                        seen.add(url)
                        urls.append(url)
                # try to fetch response body and parse JSON for urlhref
                if request_id:
                    try:
                        body = driver.execute_cdp_cmd("Network.getResponseBody", {"requestId": request_id})
                        text = ""
                        if isinstance(body, dict):
                            # body may contain 'body' and 'base64Encoded'
                            b = body.get('body', '')
                            if body.get('base64Encoded'):
                                try:
                                    text = base64.b64decode(b).decode('utf-8', errors='ignore')
                                except Exception:
                                    text = ""
                            else:
                                text = b
                        else:
                            text = str(body)
                        if text:
                            # search for obvious patterns like "urlhref":"http..." or "result":{"urlhref":"..."}
                            # try JSON parse
                            try:
                                j = json.loads(text)
                                # search nested
                                def find_url(obj):
                                    if isinstance(obj, dict):
                                        for k, v in obj.items():
                                            if isinstance(v, str) and any(v.endswith(ext) for ext in ATTACH_EXTS):
                                                return v
                                            if isinstance(v, str) and v.startswith('http') and ('down.bidcenter' in v or any(ext in v for ext in ATTACH_EXTS)):
                                                return v
                                            res = find_url(v)
                                            if res:
                                                return res
                                    elif isinstance(obj, list):
                                        for it in obj:
                                            res = find_url(it)
                                            if res:
                                                return res
                                    return None
                                found = None
                                # common field names
                                for fk in ('urlhref', 'fileUrl', 'downloadUrl', 'url'):
                                    if fk in j:
                                        v = j.get(fk)
                                        if isinstance(v, str) and v.startswith('http'):
                                            found = v
                                            break
                                if not found:
                                    # deep search
                                    found = find_url(j)
                                if found and found not in seen:
                                    seen.add(found)
                                    urls.append(found)
                            except Exception:
                                # fallback: regex search for http...xxx.pdf
                                m = re.search(r'(https?://[^\s"\'<>]+(?:' + '|'.join([re.escape(e) for e in ATTACH_EXTS]) + r'))', text, re.I)
                                if m:
                                    found = m.group(1)
                                    if found not in seen:
                                        seen.add(found)
                                        urls.append(found)
                    except Exception:
                        # CDP getResponseBody may fail; ignore
                        pass
        except Exception:
            continue
    return urls

# -------------------------
# Download wrapper: use requests session derived from driver
# -------------------------
def download_with_session(session: requests.Session, url: str, out_dir: Path, filename_override: Optional[str]=None) -> Optional[Path]:
    try:
        ensure_dir(out_dir)
        parsed_name = filename_override or derive_filename_from_url(url)
        out_path = out_dir / parsed_name
        logger.info(f"尝试下载: {url} -> {out_path.name}")
        with session.get(url, stream=True, timeout=60, allow_redirects=True) as r:
            r.raise_for_status()
            with open(out_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
        logger.info(f"下载完成: {out_path}")
        return out_path
    except Exception as e:
        logger.warning(f"下载失败: {url} -> {e}")
        return None

# -------------------------
# 解压与上传
# -------------------------
def extract_and_upload_file(path: Path, task_id: str, page_title_15: str, uploaded: List[str], messages: List[str]):
    # If zip or rar, extract then upload inner files individually with naming rule
    if path.suffix.lower() == '.zip':
        try:
            extract_dir = path.parent / f"_extracted_{path.stem}"
            ensure_dir(extract_dir)
            with zipfile.ZipFile(str(path), 'r') as zf:
                zf.extractall(str(extract_dir))
            messages.append(f"  解压 zip 成功: {path.name}")
            for root, _, files in os.walk(str(extract_dir)):
                for fi in files:
                    src = Path(root) / fi
                    stdname = f"{task_id}{page_title_15}附件{path.name}_{fi}"
                    dest = path.parent / stdname
                    shutil.copy2(src, dest)
                    ok, info = upload_file_to_bisheng(dest)
                    messages.append(f"    上传解压文件 '{dest.name}' => {ok} ({info})")
                    if ok:
                        uploaded.append(dest.name)
        except Exception as e:
            messages.append(f"  zip 解压失败: {e}")
    elif path.suffix.lower() == '.rar':
        if patoolib is None:
            messages.append("  patoolib 未安装，无法解压 rar")
            return
        try:
            extract_dir = path.parent / f"_extracted_{path.stem}"
            ensure_dir(extract_dir)
            patoolib.extract_archive(str(path), outdir=str(extract_dir))
            messages.append(f"  rar 解压成功: {path.name}")
            for root, _, files in os.walk(str(extract_dir)):
                for fi in files:
                    src = Path(root) / fi
                    stdname = f"{task_id}{page_title_15}附件{path.name}_{fi}"
                    dest = path.parent / stdname
                    shutil.copy2(src, dest)
                    ok, info = upload_file_to_bisheng(dest)
                    messages.append(f"    上传解压文件 '{dest.name}' => {ok} ({info})")
                    if ok:
                        uploaded.append(dest.name)
        except Exception as e:
            messages.append(f"  rar 解压失败: {e}")
    else:
        # normal file
        stdname = f"{task_id}{page_title_15}附件{path.name}"
        dest = path.parent / stdname
        try:
            if not dest.exists():
                shutil.copy2(path, dest)
            ok, info = upload_file_to_bisheng(dest)
            messages.append(f"  上传文件 '{dest.name}' => {ok} ({info})")
            if ok:
                uploaded.append(dest.name)
        except Exception as e:
            messages.append(f"  上传失败: {dest.name} -> {e}")

# -------------------------
# 上传单文件到毕昇
# -------------------------
def upload_file_to_bisheng(file_path: Path) -> Tuple[bool, str]:
    try:
        endpoint = f"{BISHENG_API_BASE_URL.rstrip('/')}/api/v2/filelib/file/{KNOWLEDGE_BASE_ID}"
        with open(file_path, 'rb') as fh:
            files = {'file': (file_path.name, fh, 'application/octet-stream')}
            r = requests.post(endpoint, files=files, timeout=120, verify=REQUESTS_VERIFY)
            r.raise_for_status()
            return True, f"HTTP{r.status_code}"
    except Exception as e:
        return False, f"UploadFailed: {e}"

# -------------------------
# Strategy A (robust): handle opendown -> iframe -> GetValidateCode links
# -------------------------
def strategy_iframe_popups(driver, session, task_id: str, page_title_15: str, work_dir: Path, messages: List[str]) -> List[Path]:
    messages.append("  [策略A] 尝试触发 opendown() 并解析 iframe 内附件...")
    downloaded = []
    try:
        # 1) find and click opendown triggers (anchors/buttons)
        triggers = []
        try:
            triggers = driver.find_elements(By.XPATH, "//*[contains(@onclick,'opendown')] | //a[contains(@onclick,'opendown')] | //button[contains(@onclick,'opendown')]")
        except Exception:
            triggers = []
        if triggers:
            messages.append(f"    发现 opendown 触发点 {len(triggers)} 个，依次点击...")
            for t in triggers:
                try:
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'})", t)
                    time.sleep(0.3)
                    driver.execute_script("arguments[0].click();", t)
                    time.sleep(0.4)
                except Exception as e:
                    messages.append(f"      点击 trigger 失败: {e}")
        else:
            messages.append("    未发现 opendown 触发点，继续尝试扫描页面 iframe...")

        # 2) wait for iframe(s) that likely contain attachments
        iframes = driver.find_elements(By.TAG_NAME, 'iframe')
        messages.append(f"    页面 iframe 数: {len(iframes)}")
        for idx, ifr in enumerate(iframes):
            try:
                # check iframe src to prefer attachment iframes
                src = ifr.get_attribute('src') or ''
                if not src:
                    # still try to switch
                    pass
                # try to switch to iframe
                try:
                    driver.switch_to.default_content()
                    driver.switch_to.frame(ifr)
                except Exception:
                    continue
                messages.append(f"    切入 iframe#{idx} (src={src})")
                # find candidate links inside iframe
                cand = driver.find_elements(By.XPATH, ".//a[contains(@onclick,'GetValidateCode')] | .//a[contains(@href,'.pdf') or contains(@href,'.doc') or contains(@href,'.zip') or contains(@href,'.rar')] | .//a[contains(@title,'.pdf') or contains(@title,'.doc') or contains(@title,'.zip') or contains(@title,'.rar')]")
                messages.append(f"      在 iframe#{idx} 发现候选链接: {len(cand)}")
                base_handles = set(driver.window_handles)
                for i, el in enumerate(cand):
                    try:
                        title = (el.get_attribute('title') or el.text or '').strip()[:120]
                        messages.append(f"      处理候选 {i+1}: '{title}'")
                        href = el.get_attribute('href') or ''
                        onclick = el.get_attribute('onclick') or ''
                        # Case 1: direct href to file
                        if href and href.strip().lower().startswith('http') and looks_like_attachment(href):
                            path = download_with_session(session, href, work_dir)
                            if path:
                                downloaded.append(path)
                            continue
                        # Case 2: onclick contains GetValidateCode('selid')
                        m = re.search(r"GetValidateCode\(['\"]?([^'\")]+)['\"]?\)", onclick)
                        selid = None
                        if m:
                            selid = m.group(1)
                        # click the link to trigger validation
                        try:
                            driver.execute_script("arguments[0].scrollIntoView({block:'center'})", el)
                            driver.execute_script("arguments[0].click();", el)
                        except UnexpectedAlertPresentException as e:
                            # dismiss possible alert and continue
                            try:
                                alert = driver.switch_to.alert
                                msg = alert.text
                                messages.append(f"        捕获 alert: {msg} -> dismiss")
                                alert.dismiss()
                            except Exception:
                                pass
                        except Exception as e:
                            messages.append(f"        点击失败: {e}")
                        # after click: three attempts to discover download URL
                        got_one = False
                        # A. wait for new window
                        try:
                            t0 = time.time()
                            while time.time() - t0 < CLICK_NEW_WINDOW_WAIT:
                                now_handles = set(driver.window_handles)
                                new = list(now_handles - base_handles)
                                if new:
                                    new_win = new[0]
                                    driver.switch_to.window(new_win)
                                    time.sleep(0.6)
                                    new_url = driver.current_url
                                    messages.append(f"        新窗口出现: {new_url}")
                                    if new_url and new_url.startswith('http') and looks_like_attachment(new_url):
                                        p = download_with_session(session, new_url, work_dir)
                                        if p:
                                            downloaded.append(p)
                                            got_one = True
                                    # close new window and go back
                                    try:
                                        driver.close()
                                    except Exception:
                                        pass
                                    try:
                                        driver.switch_to.window(list(base_handles)[0])
                                    except Exception:
                                        pass
                                    break
                                time.sleep(0.3)
                        except Exception as e:
                            messages.append(f"        检查新窗口异常: {e}")
                        if got_one:
                            continue
                        # B. poll performance logs + CDP getResponseBody (search json.result.urlhref)
                        perf_found = None
                        for _ in range(PERF_POLL_ITER):
                            perf_urls = extract_urls_from_perf_logs(driver)
                            if perf_urls:
                                # prefer ones that look like attachments or contain down.bidcenter
                                for pu in perf_urls:
                                    if not pu.startswith('http'):
                                        pu = urljoin(driver.current_url, pu)
                                    if looks_like_attachment(pu) or 'down.bidcenter' in pu:
                                        perf_found = pu
                                        break
                            if perf_found:
                                break
                            time.sleep(PERF_POLL_SLEEP)
                        if perf_found:
                            messages.append(f"        从 performance logs 找到: {perf_found}")
                            p = download_with_session(session, perf_found, work_dir)
                            if p:
                                downloaded.append(p)
                                got_one = True
                        if got_one:
                            continue
                        # C. inspect DOM container (selid)
                        if selid:
                            try:
                                # the page may have element with id=selid that receives a link or direct URL after validate
                                try:
                                    elc = driver.find_element(By.ID, selid)
                                    inner_html = elc.get_attribute('innerHTML') or ''
                                    # find any http link inside
                                    m2 = re.search(r'href=[\'"]([^\'"]+)[\'"]', inner_html, re.I)
                                    if m2:
                                        candidate = m2.group(1)
                                        if not candidate.startswith('http'):
                                            candidate = urljoin(driver.current_url, candidate)
                                        messages.append(f"        在容器 {selid} 中发现链接: {candidate}")
                                        p = download_with_session(session, candidate, work_dir)
                                        if p:
                                            downloaded.append(p)
                                            got_one = True
                                except Exception:
                                    pass
                            except Exception:
                                pass
                        # small pause before next candidate
                        time.sleep(0.3)
                    except Exception as e:
                        messages.append(f"      处理候选异常: {e}")
                # after processing cand, switch back to default content
                try:
                    driver.switch_to.default_content()
                except Exception:
                    pass
            except Exception as e:
                messages.append(f"    iframe#{idx} 处理异常: {e}")
            finally:
                try:
                    driver.switch_to.default_content()
                except Exception:
                    pass
    except Exception as e:
        messages.append(f"  [策略A] 异常: {e}")
    messages.append(f"  [策略A] 共下载 {len(downloaded)} 个文件")
    return downloaded

# -------------------------
# Strategy B (page straight links) - main page + detial 标签
# -------------------------
def strategy_direct_links(driver, session, task_id: str, page_title_15: str, work_dir: Path, messages: List[str]) -> List[Path]:
    downloaded = []
    messages.append("  [策略B] 页面直链与 detial 区域扫描（兜底）")
    try:
        driver.switch_to.default_content()
        # First, all <a> tags
        anchors = driver.find_elements(By.TAG_NAME, 'a')
        cand = []
        for a in anchors:
            try:
                href = a.get_attribute('href') or ''
                txt = (a.text or a.get_attribute('title') or '').strip()
            except Exception:
                continue
            if href and not href.strip().lower().startswith('javascript') and looks_like_attachment(href):
                cand.append(href)
        # Second, scan page source for href="..." patterns (some links rendered in HTML)
        try:
            html = driver.page_source or ''
            for m in re.findall(r'href=[\'"]([^\'"]+)[\'"]', html, re.I):
                if looks_like_attachment(m):
                    full = urljoin(driver.current_url, m)
                    if full not in cand:
                        cand.append(full)
        except Exception:
            pass
        messages.append(f"    直链候选数: {len(cand)}")
        for u in cand:
            try:
                path = download_with_session(session, u, work_dir)
                if path:
                    downloaded.append(path)
            except Exception as e:
                messages.append(f"    下载直链失败: {u} -> {e}")
    except Exception as e:
        messages.append(f"  [策略B] 异常: {e}")
    messages.append(f"  [策略B] 共下载 {len(downloaded)} 个文件")
    return downloaded

# -------------------------
# Main single-task processor
# -------------------------
def handle_single_task(task_id: str, url: str) -> dict:
    messages: List[str] = []
    messages.append(f"--- [附件攻坚任务] ID {task_id} 已接收 ---")
    work_dir = Path(DOWNLOAD_WORK_DIR) / task_id
    if work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)
    ensure_dir(work_dir)

    driver = None
    try:
        # create driver & session
        messages.append(f"  为本次任务创建临时下载目录: {work_dir}")
        driver = build_driver(download_dir=work_dir, headless=HEADLESS)
        messages.append("  浏览器启动成功")
        driver.get(url)
        time.sleep(1.0)
        # get page title (h3优先)
        try:
            h3s = driver.find_elements(By.TAG_NAME, 'h3')
            page_title = ""
            for h in h3s:
                t = (h.text or "").strip()
                if t:
                    page_title = t
                    break
            if not page_title:
                page_title = driver.title or ""
        except Exception:
            page_title = driver.title or ""
        page_title_15 = short_title(page_title)
        messages.append(f"  页面标题(前15): {page_title_15}")

        session = session_from_driver(driver)

        # Strategy A
        messages.append("  开始执行 策略A (iframe 弹窗内 GetValidateCode / 直链)")
        downloaded_a = strategy_iframe_popups(driver, session, task_id, page_title_15, work_dir, messages)
        messages.append(f"  策略A 获取文件数: {len(downloaded_a)}")

        # Strategy B if no files
        downloaded_b = []
        if not downloaded_a:
            messages.append("  策略A 未获取到文件，开始执行 策略B (主页面直链)")
            downloaded_b = strategy_direct_links(driver, session, task_id, page_title_15, work_dir, messages)
            messages.append(f"  策略B 获取文件数: {len(downloaded_b)}")
        else:
            messages.append("  策略A 已获取文件，跳过 策略B")

        downloaded = downloaded_a + downloaded_b
        if not downloaded:
            messages.append(f"任务ID {task_id}: 未找到任何附件。")
            return {'status': 'no_content', 'message': f"任务ID {task_id}: 未找到任何附件。", 'messages': messages}

        # 处理下载并上传
        uploaded = []
        for f in downloaded:
            if not f or not Path(f).exists():
                messages.append(f"  文件缺失: {f}")
                continue
            extract_and_upload_file(Path(f), task_id, page_title_15, uploaded, messages)

        messages.append(f"--- [附件攻坚任务] ID {task_id} 结束: 上传 {len(uploaded)} 件 ---")
        return {'status': 'success', 'report': f"上传 {len(uploaded)} 件", 'uploaded': uploaded, 'messages': messages}

    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"!!! 任务ID {task_id} 崩溃: {e}\n{tb}")
        messages.append(f"!!! 任务失败: {e}")
        try:
            if driver:
                driver.quit()
        except Exception:
            pass
        return {'status': 'error', 'message': str(e), 'messages': messages}
    finally:
        try:
            if driver:
                driver.quit()
        except Exception:
            pass

# -------------------------
# Flask routes
# -------------------------
@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'time': int(time.time())})

@app.route('/upload_to_bisheng', methods=['POST'])
def upload_to_bisheng():
    try:
        data = request.get_json(force=True, silent=True) or {}
    except Exception:
        data = {}
    tasks = []
    if 'ID' in data and 'url' in data:
        tasks = [data]
    elif 'arg1' in data and isinstance(data['arg1'], str):
        for ln in data['arg1'].splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                obj = json.loads(ln)
                if 'ID' in obj and 'url' in obj:
                    tasks.append(obj)
            except Exception:
                logger.warning(f"未能解析行: {ln[:120]}")
    else:
        # try raw body parsing
        try:
            raw = request.data.decode('utf-8')
            if raw:
                try:
                    dd = json.loads(raw)
                    if 'arg1' in dd and isinstance(dd['arg1'], str):
                        for ln in dd['arg1'].splitlines():
                            ln = ln.strip()
                            if not ln: continue
                            try:
                                obj = json.loads(ln)
                                if 'ID' in obj and 'url' in obj:
                                    tasks.append(obj)
                            except Exception:
                                continue
                except Exception:
                    pass
        except Exception:
            pass

    if not tasks:
        return jsonify({'status': 'error', 'message': '缺少任务: 需要 {ID,url} 或 {arg1} 多行JSON'}), 400

    results = []
    reports = []
    for t in tasks:
        tid = str(t.get('ID'))
        url = str(t.get('url'))
        logger.info(f"--- [附件攻坚任务] ID {tid} 已接收 ---")
        res = handle_single_task(tid, url)
        # write messages into mission log
        for m in res.get('messages', []):
            logger.info(m)
        if res.get('status') == 'success':
            reports.append(f"ID {tid}: 上传 {len(res.get('uploaded', []))} 件 -> {', '.join(res.get('uploaded', [])[:3])}{'...' if len(res.get('uploaded', []))>3 else ''}")
        elif res.get('status') == 'no_content':
            reports.append(f"ID {tid}: 未找到任何附件")
        else:
            reports.append(f"ID {tid}: 失败 - {res.get('message','')}")
        results.append({'ID': tid, 'url': url, **res})

    return jsonify({'status': 'ok', 'report': "\n".join(reports), 'details': results}), 200

# -------------------------
# main
# -------------------------
if __name__ == '__main__':
    ensure_dir(TEMP_DOWNLOAD_DIR)
    app.run(host=SERVER_BIND_IP, port=SERVER_PORT)
