"""Unit tests of the dashboard's ES modules, run in the browser: each test imports modules into the page."""

from __future__ import annotations

import json
from typing import Any

import pytest
from playwright.sync_api import Page

from poster.content import normalize_content, normalize_hashtag


@pytest.fixture
def js(demo: Page) -> Any:
    """Runs an async function body with ``m(name)`` importing static/js/<name>.js."""
    base = demo.evaluate("document.querySelector('script[type=module]').src.replace(/main\\.js$/, '')")

    def run(body: str, arg: Any = None) -> Any:
        return demo.evaluate(f"async ([base, arg]) => {{ const m = (name) => import(base + name + '.js'); {body} }}", [base, arg])

    return run


HASHTAGS = ["#বাংলাদেশ", "हिन्दी", "#தமிழ்", "#café", "café", "#hello-world!", "##snake_case", "2026", "!!!", "#Ünïcödé", "#東京"]


def test_hashtags_match_the_server(js: Any) -> None:
    browser = js("""
      const { ValidationService } = await m('validation');
      const v = new ValidationService(() => ({}));
      return arg.map((tag) => v.normalizeHashtag(tag));
    """, HASHTAGS)
    assert browser == [normalize_hashtag(tag) for tag in HASHTAGS]


REPLIES = [
    {"description": "সুন্দর সকাল ঢাকায় #বাংলাদেশ #ঢাকা", "alternative_description": "Morning in Dhaka", "hashtags": [],
     "category": "travel", "engagement_score": 0.9},
    {"caption": "Coffee time is the best time of day #coffee #latte", "tags": "cafe, morning", "score": "73", "categories": ["food"]},
    {"description": "\"Quoted caption\"", "hashtags": ["#" + str(i) + "x" for i in range(20)], "category": "  art  "},
]


@pytest.mark.parametrize("reply", REPLIES)
def test_generated_content_matches_the_server(js: Any, reply: dict[str, Any]) -> None:
    browser = js("""
      const { ValidationService } = await m('validation');
      const v = new ValidationService(() => ({ descMax: 2200 }));
      return v.normalizeGeneratedContent(arg, { descMax: 2200 });
    """, reply)
    content, issues = normalize_content(reply, 2200)
    assert browser["content"] == content
    assert browser["issues"] == issues


def test_rules_come_from_the_server(js: Any, demo: Page) -> None:
    browser = js("const { RULES, LIMITS, TONES } = await m('config'); return { RULES, maxHashtags: LIMITS.maxHashtags, TONES };")
    server = demo.evaluate("fetch('/api/config').then((r) => r.json())")["rules"]
    assert browser["RULES"] == server
    assert browser["maxHashtags"] == server["hashtags"]["max"] and browser["TONES"] == server["tones"]


def test_timezones_and_cached_formatters(js: Any) -> None:
    result = js("""
      const { TimeZoneUtil, dateTimeFormat } = await m('time');
      const gap = TimeZoneUtil.zonedToUtc('2026-03-08T02:30', 'America/New_York');
      const ok = TimeZoneUtil.zonedToUtc('2026-07-01T09:15', 'Asia/Dhaka');
      return {
        gapExists: gap.exists, dhaka: ok.date.toISOString(), back: TimeZoneUtil.toLocalInput(ok.date, 'Asia/Dhaka'),
        valid: [TimeZoneUtil.isValid('Asia/Dhaka'), TimeZoneUtil.isValid('Mars/Base')],
        cached: dateTimeFormat('en-US', { timeZone: 'UTC' }) === dateTimeFormat('en-US', { timeZone: 'UTC' })
      };
    """)
    assert result == {"gapExists": False, "dhaka": "2026-07-01T03:15:00.000Z", "back": "2026-07-01T09:15",
                      "valid": [True, False], "cached": True}


def test_gateway_timeout_is_final(js: Any) -> None:
    error = js("""
      const { ApiService } = await m('api');
      const e = ApiService.errorFromResponse(504, null, 'Ollama');
      const proxy = ApiService.errorFromResponse(502, null, 'Ollama');
      return { kind: e.kind, fromServer: e.fromServer, retryable: e.retryable, uncertain: e.uncertain, proxyRetryable: proxy.retryable };
    """)
    assert error == {"kind": "timeout", "fromServer": True, "retryable": False, "uncertain": True, "proxyRetryable": True}


def test_server_errors_carry_uncertainty(js: Any) -> None:
    error = js("""
      const { ApiService } = await m('api');
      const e = ApiService.errorFromResponse(502, { error: { message: 'lost', kind: 'network', uncertain: true } }, 'Buffer');
      return { kind: e.kind, uncertain: e.uncertain, fromServer: e.fromServer };
    """)
    assert error == {"kind": "network", "uncertain": True, "fromServer": True}


RECONCILE = """
  const { SchedulerService } = await m('scheduler');
  const { ApiError } = await m('errors');
  const dueAt = new Date(Date.now() + 864e5);
  const post = { text: 'Hello #world', channelIds: ['ch1', 'ch2'], dueAt, mediaUrl: null };
  const existing = [
    { postId: 'p1', channelId: 'ch1', text: 'Hello #world', dueAt: dueAt.toISOString(), status: 'scheduled' },
    { postId: 'p0', channelId: 'ch2', text: 'Something else', dueAt: dueAt.toISOString(), status: 'scheduled' }
  ];
  const notifier = { log() {} };
  const scheduler = Object.assign(Object.create(SchedulerService.prototype), { notifier });
  const buffer = {
    listCalls: 0,
    async schedulePost() { return arg.mode(ApiError); },
    async listPosts() { this.listCalls += 1; return { posts: existing, next: null }; }
  };
"""


def test_lost_answer_is_reconciled(js: Any) -> None:
    result = js(RECONCILE.replace("arg.mode(ApiError)", "(() => { throw new ApiError('lost', { kind: 'timeout' }); })()") + """
      const out = await scheduler.createPosts(buffer, post, {});
      return { posts: out.posts.map((p) => p.postId), failures: out.failures.map((f) => f.channelId), lists: buffer.listCalls };
    """)
    assert result == {"posts": ["p1"], "failures": ["ch2"], "lists": 1}


def test_uncertain_partial_failure_is_reconciled(js: Any) -> None:
    partial = "({ posts: [{ channelId: 'ch2', postId: 'p2' }], failures: [{ channelId: 'ch1', message: 'lost', uncertain: true }] })"
    result = js(RECONCILE.replace("arg.mode(ApiError)", partial) + """
      const out = await scheduler.createPosts(buffer, post, {});
      return { posts: out.posts.map((p) => p.postId), failures: out.failures.length };
    """)
    assert result == {"posts": ["p2", "p1"], "failures": 0}


def test_certain_failures_are_not_looked_up(js: Any) -> None:
    result = js(RECONCILE.replace("arg.mode(ApiError)", "(() => { throw new ApiError('bad', { kind: 'validation', fromServer: true }); })()") + """
      try { await scheduler.createPosts(buffer, post, {}); return 'no error'; } catch (e) { return [e.message, buffer.listCalls]; }
    """)
    assert result == ["bad", 0]


def test_quick_filter_changes_wait_for_the_load_in_flight(js: Any) -> None:
    result = js("""
      const { BufferPostsFeed } = await m('feed');
      const calls = [];
      const buffer = { isDemo: true, listPosts: ({ filter }) => new Promise((resolve) => calls.push({ filter, resolve })) };
      const feed = new BufferPostsFeed({ notifier: { log() {} }, store: {}, getBuffer: () => buffer });
      const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
      feed.load();
      feed.setFilter('upcoming');
      feed.setFilter('published');
      const whileFirstRuns = calls.length;
      calls[0].resolve({ posts: [{ postId: 'old', status: 'scheduled' }], next: null });
      await tick();
      const requested = calls.map((c) => c.filter);
      calls[1].resolve({ posts: [{ postId: 'new', status: 'sent' }], next: null });
      await tick();
      return { whileFirstRuns, requested, posts: feed.posts.map((p) => p.postId), loading: feed.loading };
    """)
    assert result == {"whileFirstRuns": 1, "requested": ["all", "published"], "posts": ["new"], "loading": False}


def test_reconciliation_reads_pages_until_the_publish_time(js: Any) -> None:
    result = js("""
      const { SchedulerService } = await m('scheduler');
      const day = 864e5;
      const dueAt = new Date(Date.now() + 10 * day);
      const at = (days) => new Date(dueAt.getTime() + days * day).toISOString();
      const pages = [
        { posts: [{ postId: 'a', channelId: 'ch1', text: 'Other', dueAt: at(-5) }], next: { org1: '1' } },
        { posts: [{ postId: 'b', channelId: 'ch1', text: 'Hello', dueAt: dueAt.toISOString(), status: 'scheduled' }], next: { org1: '2' } },
        { posts: [{ postId: 'c', channelId: 'ch2', text: 'Hello', dueAt: at(2) }], next: { org1: '3' } },
        { posts: [{ postId: 'never', channelId: 'ch1', text: 'Hello', dueAt: dueAt.toISOString() }], next: null }
      ];
      const requested = [];
      const buffer = { async listPosts({ after }) { requested.push(after); return pages[requested.length - 1]; } };
      const found = await Object.create(SchedulerService.prototype).findCreatedPosts(buffer, { text: 'Hello', dueAt }, ['ch1', 'ch2']);
      return { found: found.map((p) => p.postId), pages: requested.length };
    """)
    assert result == {"found": ["b"], "pages": 3}


def test_server_config_is_parsed_once(js: Any) -> None:
    result = js("const a = await m('config'); const b = await m('config'); return a.SERVER === b.SERVER && Object.isFrozen(a.RULES);")
    assert result is True


def test_public_api_is_on_window(demo: Page) -> None:
    names = demo.evaluate("['generateForAllImages', 'scheduleAllPosts', 'exportConfig', 'showToast'].map((n) => typeof window[n])")
    assert names == ["function"] * 4
    assert json.loads(demo.evaluate("JSON.stringify(window.poster.settings.mode)")) == "demo"
