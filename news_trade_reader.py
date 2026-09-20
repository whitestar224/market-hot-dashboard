"""Independent desktop reader: no alert receipt, sound, signing or auto-close."""
import json
from pathlib import Path
import queue
import re
import threading
import time
import urllib.request
import urllib.error
from urllib.parse import parse_qs, urlsplit
import webbrowser

from news_trade_explanations import KEY_RE


ANALYSIS_FIELDS = (
    ("一句话判断", ("summary",)),
    ("它到底是什么", ("asset",)),
    ("背后事件起因", ("underlyingEvent", "cause")),
    ("为什么现在火", ("whyHot", "process")),
    ("核心叙事", ("narrative",)),
    ("潜在机会", ("opportunity", "impact")),
    ("后续验证信号", ("validation",)),
    ("具体机制怎么运转（大白话）", ("plainMechanism",)),
    ("最容易搞混的地方", ("confusionPoint",)),
    ("容易混淆的概念", ("conceptDistinctions",)),
    ("失效条件", ("invalidation",)),
    ("主要风险", ("risk",)),
    ("证据与推断边界", ("evidence",)),
    ("仍需核对", ("uncertainty",)),
)


def centered_geometry(area, width=1040, height=780):
    left, top, right, bottom = area
    width = max(1, min(width, right-left-48))
    height = max(1, min(height, bottom-top-64))
    return f"{width}x{height}{left+(right-left-width)//2:+d}{top+(bottom-top-height)//2:+d}"


def safe_post_url(value):
    return str(value) if re.fullmatch(r"https://x\.com/[A-Za-z0-9_]{1,15}/status/\d{5,25}", str(value)) else ""


def safe_search_url(value):
    try:
        parsed = urlsplit(str(value))
        query = parse_qs(parsed.query, keep_blank_values=False)
    except (TypeError, ValueError):
        return ""
    if (parsed.scheme != 'https' or parsed.netloc != 'x.com' or parsed.path != '/search'
            or parsed.fragment or set(query) - {'q','f'} or query.get('f') != ['live']
            or len(query.get('q', [''])[0]) not in range(1, 161)):
        return ""
    return str(value)


def reader_log(key, event, **fields):
    """Local UI action trace, never credentials or tweet text."""
    try:
        path = Path(__file__).resolve().parent / '.runtime-cache' / 'explanation_reader.log'
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as log:
            log.write(json.dumps({'time':time.time(),'key':key,'event':event,**fields},ensure_ascii=False)+'\n')
    except OSError:
        pass


def reader_payload(opener, key, port, retry=False):
    """Only one fixed loopback endpoint, bounded data and safe actionable errors."""
    url = f'http://127.0.0.1:{int(port)}/api/news-trade/explanations'
    request = (urllib.request.Request(url+'/retry', data=json.dumps({'id':key}).encode(),
        headers={'Content-Type':'application/json'}, method='POST') if retry else url+'?id='+key)
    try:
        with opener.open(request, timeout=12) as response:
            payload=json.loads(response.read(180001))
        if not isinstance(payload,dict) or payload.get('status') not in {'ready','pending','unavailable','links','analysis','empty'}:
            raise ValueError('invalid reader response')
        return payload
    except urllib.error.HTTPError as exc:
        messages={400:'原事件信息不可用，请从对应标的的弹窗重新打开',403:'本机解释推文访问被拒绝',
                  404:'本机服务尚未加载解释推文接口，请更新服务',429:'重试过于频繁，请一分钟后再试',
                  503:'解释推文服务繁忙，请稍后重试'}
        allowed={'解释推文编号无效','此事件缺少可核对的原始信息，请从新弹窗打开','解释推文队列忙碌，请稍后重试'}
        try: detail=json.loads(exc.read(4096)).get('error')
        except Exception: detail=''
        finally: exc.close()
        message=detail if detail in allowed else messages.get(exc.code,f'本机服务返回 HTTP {exc.code}，请稍后重试')
        reader_log(key,'request_failed',httpStatus=exc.code,retry=retry)
        return {'status':'unavailable','message':message,'httpStatus':exc.code}
    except (TimeoutError, urllib.error.URLError, OSError):
        reader_log(key,'connection_failed',retry=retry)
        # A dashboard restart is normally shorter than an explanation search.
        # Keep the reader inside its existing polling window so a transient
        # disconnect cannot replace a nearly-complete result with a terminal
        # error page. The final timeout below still gives a clear failure when
        # the local service stays unavailable for the full recovery window.
        return {
            'status':'pending',
            'message':'本机服务正在恢复，正在自动重连…',
            'connectionFailed':True,
        }
    except (ValueError, TypeError):
        reader_log(key,'invalid_response',retry=retry)
        return {'status':'unavailable','message':'本机服务返回格式异常，请重试'}


def show_reader(key, port=8765, title=''):
    if not KEY_RE.fullmatch(str(key)) or not 1 <= int(port) <= 65535:
        return 2
    import tkinter as tk
    from desktop_alert import work_area

    root = tk.Tk()
    last_title = str(title or '')[:120]
    reader_log(key,'opened',title=last_title,port=int(port))
    root.title(f"星云社 · {last_title + ' · ' if last_title else ''}解释推文")
    root.configure(bg="#ffffff")
    root.geometry(centered_geometry(work_area(root)))
    root.minsize(480, 360)
    root.lift()
    root.attributes("-topmost", True)
    root.after(900, lambda: root.attributes("-topmost", False))
    root.bind("<Escape>", lambda _e: root.destroy())
    header = tk.Frame(root, bg="#ffffff", padx=28, pady=20)
    header.pack(fill="x")
    heading = tk.Label(header, text=f"{last_title + ' · ' if last_title else ''}解释推文", bg="#ffffff", fg="#0f1419", anchor="w",
                       wraplength=840, justify='left',
                       font=("Microsoft YaHei UI", 21, "bold"))
    heading.pack(fill="x")
    status = tk.Label(header, text="读取提前筛选的内容…", bg="#ffffff", fg="#536471", anchor="w",
                      font=("Microsoft YaHei UI", 11), wraplength=840, justify="left")
    status.pack(fill="x", pady=(9, 0))
    host = tk.Frame(root, bg="#eef3f6")
    host.pack(fill="both", expand=True)
    scrollbar = tk.Scrollbar(host)
    scrollbar.pack(side="right", fill="y")
    canvas = tk.Canvas(host, bg="#eef3f6", highlightthickness=0, yscrollcommand=scrollbar.set)
    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.configure(command=canvas.yview)
    body = tk.Frame(canvas, bg="#eef3f6")
    window = canvas.create_window((0, 0), window=body, anchor="nw")
    wrap_labels = []

    def resize(event):
        canvas.itemconfigure(window, width=event.width)
        for label in wrap_labels:
            label.configure(wraplength=max(300, event.width-100))
        status.configure(wraplength=max(300, event.width-64))

    canvas.bind("<Configure>", resize)
    body.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
    root.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(-int(e.delta/120), "units"))

    def render(payload):
        nonlocal last_title
        last_title = str(payload.get('title') or last_title)[:120]
        label = f"{last_title + ' · ' if last_title else ''}解释推文"
        root.title(f"星云社 · {label}")
        heading.configure(text=label)
        status.configure(text=payload.get("message") or "暂时没有可展示的内容")
        for child in body.winfo_children():
            child.destroy()
        wrap_labels.clear()
        posts = [p for p in payload.get("posts") or [] if safe_post_url(p.get("url"))][:5]
        ai_explanation = payload.get('aiExplanation') if isinstance(payload.get('aiExplanation'), dict) else {}

        def card(post, index):
            box = tk.Frame(body, bg="#ffffff", highlightbackground="#cfd9de", highlightthickness=1, padx=24, pady=20)
            box.pack(fill="x", padx=24, pady=(16, 0))
            def label(text, size=12, color="#203442", bold=False):
                widget = tk.Label(box, text=text, anchor="w", justify="left", bg="#ffffff", fg=color,
                                  font=("Microsoft YaHei UI", size, "bold" if bold else "normal"),
                                  wraplength=max(300, canvas.winfo_width()-100))
                widget.pack(fill="x", pady=(0, 10))
                wrap_labels.append(widget)
            author_row = tk.Frame(box, bg='#ffffff')
            author_row.pack(fill='x', pady=(0, 16))
            avatar = tk.Canvas(author_row, width=48, height=48, bg='#ffffff', highlightthickness=0)
            avatar.pack(side='left', padx=(0, 12))
            avatar.create_oval(1,1,47,47,fill='#eff3f4',outline='')
            avatar.create_text(24,24,text=str(post.get('author') or '?')[:1],fill='#536471',font=('Microsoft YaHei UI',18,'bold'))
            names = tk.Frame(author_row,bg='#ffffff')
            names.pack(side='left',fill='x',expand=True)
            tk.Label(names,text=post.get('author') or post.get('handle'),bg='#ffffff',fg='#0f1419',anchor='w',
                     font=('Microsoft YaHei UI',14,'bold')).pack(fill='x')
            tk.Label(names,text='@'+str(post.get('handle') or ''),bg='#ffffff',fg='#536471',anchor='w',
                     font=('Microsoft YaHei UI',11)).pack(fill='x')
            tk.Label(author_row,text='𝕏',bg='#ffffff',fg='#0f1419',font=('Segoe UI Symbol',25)).pack(side='right')
            stamp = time.strftime("%m-%d %H:%M", time.localtime((post.get("publishedAt") or 0)/1000))
            followers = int(post.get("followers") or 0)
            author_info = stamp
            if followers:
                author_info += f" · {followers:,} 粉丝"
            if post.get("followedAuthor"):
                author_info += " · 已关注作者"
            excerpt = str(post.get("text") or "")
            if post.get('sourceType') == 'codex-search-excerpt':
                label('本地 Codex 检索摘录 · 原文可在 X 核对', 11, '#536471')
            elif post.get('sourceType') == 'codex-indexed-excerpt':
                label('搜索索引摘录 · 非完整原文，可在 X 核对', 11, '#536471')
            elif post.get('sourceType') == 'previously-saved-post':
                label('此前按同一链与 CA 保存的相关原帖 · 可在 X 核对', 11, '#536471')
            label(excerpt, 15, '#0f1419')
            label(author_info, 11, '#536471')
            tk.Frame(box,bg='#eff3f4',height=1).pack(fill='x',pady=(2,12))
            label(f"阅读提示 · {post.get('focus') or '解释推文'}：{post.get('reason') or ''}", 11, '#536471')
            tk.Button(box, text="在 X 查看原推文 ↗", command=lambda: webbrowser.open(safe_post_url(post["url"])),
                      bg="#eff3f4", fg="#0f1419", relief="flat", padx=15, pady=8, cursor="hand2",
                      font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w")

        if ai_explanation:
            analysis_box = tk.Frame(body, bg="#ffffff", highlightbackground="#cfd9de", highlightthickness=1,
                                    padx=24, pady=20)
            analysis_box.pack(fill="x", padx=24, pady=(16, 0))
            provisional = ai_explanation.get('sourceType') == 'local-monitor-context'
            analysis_title = ("监控数据快速说明 · 本地 Codex 正在补充" if provisional else
                              "AI 事件、叙事与机会解读 · 非 X 推文")
            tk.Label(analysis_box, text=analysis_title, anchor="w", bg="#ffffff", fg="#536471",
                     font=("Microsoft YaHei UI", 11, "bold")).pack(fill="x", pady=(0, 14))
            for title_text, field_names in ANALYSIS_FIELDS:
                value = next((str(ai_explanation.get(field) or '').strip()
                              for field in field_names if str(ai_explanation.get(field) or '').strip()), '')
                if not value:
                    continue
                tk.Label(analysis_box, text=title_text, anchor="w", bg="#ffffff", fg="#0f1419",
                         font=("Microsoft YaHei UI", 12, "bold")).pack(fill="x", pady=(5, 3))
                detail = tk.Label(analysis_box, text=value, anchor="w", justify="left", bg="#ffffff", fg="#203442",
                                  font=("Microsoft YaHei UI", 12),
                                  wraplength=max(300, canvas.winfo_width()-100))
                detail.pack(fill="x", pady=(0, 8))
                wrap_labels.append(detail)
            if provisional and payload.get('retryable'):
                def retry_provisional():
                    reader_log(key,'retry_clicked',title=last_title)
                    retry_provisional_button.configure(state='disabled',text='正在重新检查…')
                    threading.Thread(target=fetch,args=(True,),daemon=True,name='explanation-retry').start()
                retry_provisional_button = tk.Button(
                    analysis_box, text='重新搜索 / 补充', command=retry_provisional,
                    relief='flat', bg='#dbe9f0', font=('Microsoft YaHei UI',11,'bold'), padx=18, pady=9)
                retry_provisional_button.pack(anchor='w', pady=(8, 0))
        if not posts and not ai_explanation:
            status.configure(text="")  # One clear error, not two copies of it.
            tk.Label(body, text=payload.get("message") or "暂未找到高质量解释推文", bg="#eef3f6", fg="#344f60",
                     font=("Microsoft YaHei UI", 15), wraplength=700, justify="left").pack(padx=32, pady=56)
            if payload.get('status') in {'unavailable','empty','links'}:
                def retry():
                    reader_log(key,'retry_clicked',title=last_title)
                    retry_button.configure(state='disabled',text='正在重新检查…')
                    threading.Thread(target=fetch,args=(True,),daemon=True,name='explanation-retry').start()
                retry_button = tk.Button(body,text='重新搜索 / 复核',command=retry,relief='flat',bg='#dbe9f0',
                    font=('Microsoft YaHei UI',13,'bold'),padx=24,pady=12)
                retry_button.pack(pady=(0,24))
        for index, post in enumerate(posts[:3], 1):
            card(post, index)
        links = [row for row in payload.get('links', []) if isinstance(row, dict) and safe_post_url(row.get('url'))]
        if links:
            link_box = tk.Frame(body, bg='#ffffff', padx=24, pady=20)
            link_box.pack(fill='x', padx=24, pady=16)
            tk.Label(link_box, text='相关推文链接 · 正文未核验', bg='#ffffff', fg='#536471', anchor='w',
                     font=('Microsoft YaHei UI',12)).pack(fill='x',pady=(0,12))
            for link in links[:5]:
                tk.Button(link_box, text=f"{link.get('title') or '在 X 查看相关推文'}  ↗", anchor='w',
                          wraplength=800, justify='left', relief='flat', bg='#eff3f4', fg='#1675ac',
                          font=('Microsoft YaHei UI',13,'bold'), padx=16, pady=12, cursor='hand2',
                          command=lambda url=link['url']: webbrowser.open(safe_post_url(url))).pack(fill='x',pady=(0,8))
        searches = [row for row in payload.get('searchLinks', []) if isinstance(row, dict) and safe_search_url(row.get('url'))]
        if searches and not posts:
            search_box = tk.Frame(body, bg='#eef3f6')
            search_box.pack(fill='x', padx=24, pady=(0,16))
            tk.Label(search_box, text='不等后台，直接去 X 搜索', bg='#eef3f6', fg='#536471', anchor='w',
                     font=('Microsoft YaHei UI',11)).pack(fill='x',pady=(0,8))
            for link in searches[:2]:
                tk.Button(search_box, text=f"{link.get('label') or '在 X 搜索'}  ↗", relief='flat',
                          bg='#dbe9f0', fg='#1675ac', font=('Microsoft YaHei UI',12,'bold'), padx=14,pady=10,
                          command=lambda url=link['url']: webbrowser.open(safe_search_url(url))).pack(side='left',padx=(0,10))
        if len(posts) > 3:
            def expand():
                more.destroy()
                for index, post in enumerate(posts[3:], 4):
                    card(post, index)
            more = tk.Button(body, text=f"再看 {len(posts)-3} 篇", command=expand, relief="flat", bg="#dbe9f0",
                             font=("Microsoft YaHei UI", 12, "bold"), pady=12)
            more.pack(fill="x", padx=24, pady=18)

    results = queue.Queue(maxsize=2)
    stopped = threading.Event()

    def fetch(retry=False):
        # This reader can only read one cache key from this machine, never any
        # external URL from a tweet. Disallow redirects and environment proxies.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *_args, **_kwargs):
                return None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        if retry:
            payload=reader_payload(opener,key,port,retry=True)
            if payload.get('connectionFailed'):
                # The POST may have been accepted before its response timed out.
                # Check existing state, never blindly enqueue another retry.
                payload=reader_payload(opener,key,port)
            try: results.put_nowait(payload)
            except queue.Full: pass
            if payload.get('status') != 'pending':
                return
        attempts = 0
        while not stopped.is_set():
            if stopped.is_set():
                return
            payload=reader_payload(opener,key,port)
            try:
                results.put_nowait(payload)
            except queue.Full:
                pass
            if payload.get("status") != "pending":
                return
            attempts += 1
            # Keep an explicitly opened reader alive across longer local model
            # queues and repeated dashboard reloads. Poll more gently after the
            # first three minutes; closing the window still stops immediately.
            if stopped.wait(2 if attempts < 90 else 5):
                return

    def poll():
        try:
            payload = results.get_nowait()
            render(payload)
        except queue.Empty:
            pass
        root.after(150, poll)

    threading.Thread(target=fetch, daemon=True, name="explanation-reader").start()
    root.after(100, poll)
    try:
        root.mainloop()
    finally:
        reader_log(key,'closed',title=last_title)
        stopped.set()
        try:
            for callback in root.tk.call('after', 'info'):
                root.after_cancel(callback)
        except tk.TclError:
            pass
    return 0
