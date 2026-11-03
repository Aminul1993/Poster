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


HASHTAGS = ["#বাংলাদেশ", "हिन्दी", "#தமிழ்", "#র\u200d্যালি", "#\u200cনাম\u200d", "#café", "café", "#hello-world!",
            "##snake_case", "2026", "!!!", "#Ünïcödé", "#東京"]


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
        cached: dateTimeFormat('en-US', { timeZone: 'UTC' }) === dateTimeFormat('en-US', { timeZone: 'UTC' }),
        modern: [TimeZoneUtil.modern('Asia/Calcutta'), TimeZoneUtil.modern('Asia/Dhaka')]
      };
    """)
    assert result["modern"] == ["Asia/Kolkata", "Asia/Dhaka"]
    del result["modern"]
    assert result == {"gapExists": False, "dhaka": "2026-07-01T03:15:00.000Z", "back": "2026-07-01T09:15",
                      "valid": [True, False], "cached": True}


def test_gateway_timeout_is_final(js: Any) -> None:
    error = js("""
      const { ApiService } = await m('api');
      const e = ApiService.errorFromResponse(504, null, 'Groq');
      const proxy = ApiService.errorFromResponse(502, null, 'Groq');
      return { kind: e.kind, fromServer: e.fromServer, retryable: e.retryable, uncertain: e.uncertain, proxyRetryable: proxy.retryable };
    """)
    assert error == {"kind": "timeout", "fromServer": True, "retryable": False, "uncertain": True, "proxyRetryable": True}


def test_server_errors_carry_uncertainty(js: Any) -> None:
    error = js("""
      const { ApiService } = await m('api');
      const e = ApiService.errorFromResponse(502, { error: { message: 'lost', kind: 'network', uncertain: true } }, 'Meta');
      return { kind: e.kind, uncertain: e.uncertain, fromServer: e.fromServer };
    """)
    assert error == {"kind": "network", "uncertain": True, "fromServer": True}


RESEND = """
  const { SchedulerService } = await m('scheduler');
  const { ApiError } = await m('errors');
  const dueAt = new Date(Date.now() + 864e5);
  const post = { text: 'Hello #world', channelIds: ['ch1', 'ch2'], dueAt, mediaUrl: null };
  const notifier = { log() {} };
  const scheduler = Object.assign(Object.create(SchedulerService.prototype), { notifier });
  const answers = arg;
  const social = {
    keys: [],
    async schedulePost(sent) {
      this.keys.push(sent.requestKey);
      const answer = answers[this.keys.length - 1];
      if (answer.error) throw new ApiError(answer.error, answer.options);
      return { posts: [{ channelId: 'ch1', postId: 'p1' }, { channelId: 'ch2', postId: 'p2' }], failures: [] };
    }
  };
"""


def test_a_lost_answer_is_sent_again_with_the_same_key(js: Any) -> None:
    result = js(RESEND + """
      const out = await scheduler.createPosts(social, post, {});
      return { posts: out.posts.map((p) => p.postId), sent: social.keys.length, sameKey: social.keys[0] === social.keys[1], key: social.keys[0] };
    """, [{"error": "lost", "options": {"kind": "timeout"}}, {}])
    assert result["posts"] == ["p1", "p2"] and result["sent"] == 2 and result["sameKey"] is True
    assert result["key"].startswith("req_")


def test_an_uncertain_server_error_is_sent_again(js: Any) -> None:
    result = js(RESEND + """
      const out = await scheduler.createPosts(social, post, {});
      return { posts: out.posts.length, sent: social.keys.length };
    """, [{"error": "gateway", "options": {"kind": "server", "fromServer": True, "uncertain": True}}, {}])
    assert result == {"posts": 2, "sent": 2}


def test_certain_failures_are_not_sent_again(js: Any) -> None:
    result = js(RESEND + """
      try { await scheduler.createPosts(social, post, {}); return 'no error'; } catch (e) { return [e.message, social.keys.length]; }
    """, [{"error": "bad", "options": {"kind": "validation", "fromServer": True}}, {}])
    assert result == ["bad", 1]


def test_a_second_lost_answer_stands(js: Any) -> None:
    result = js(RESEND + """
      try { await scheduler.createPosts(social, post, {}); return 'no error'; } catch (e) { return [e.message, social.keys.length]; }
    """, [{"error": "lost", "options": {"kind": "network"}}, {"error": "lost again", "options": {"kind": "network"}}])
    assert result == ["lost again", 2]


def test_quick_filter_changes_wait_for_the_load_in_flight(js: Any) -> None:
    result = js("""
      const { PostsFeed } = await m('feed');
      const calls = [];
      const social = { isDemo: true, listPosts: ({ filter }) => new Promise((resolve) => calls.push({ filter, resolve })) };
      const feed = new PostsFeed({ notifier: { log() {} }, store: {}, getSocial: () => social });
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


def test_instagram_limits(js: Any) -> None:
    result = js("""
      const { instagramProblem } = await m('services/social');
      const tags = Array.from({ length: 31 }, (_, i) => `#tag${i}`).join(' ');
      return [
        instagramProblem('Hello #world', true),
        instagramProblem('Hello', false),
        instagramProblem('x'.repeat(2201), true),
        instagramProblem('😀'.repeat(2200), true),
        instagramProblem(`Hello ${tags}`, true),
        instagramProblem('#সমুদ্র #সমুদ্র', true)
      ];
    """)
    assert result == [None, "Instagram posts need an image.", "Instagram captions are limited to 2,200 characters.", None,
                      "Instagram allows at most 30 hashtags per post.", None]


def test_server_config_is_parsed_once(js: Any) -> None:
    result = js("const a = await m('config'); const b = await m('config'); return a.SERVER === b.SERVER && Object.isFrozen(a.RULES);")
    assert result is True


def test_public_api_is_on_window(demo: Page) -> None:
    names = demo.evaluate("['generateForAllImages', 'scheduleAllPosts', 'exportConfig', 'showToast'].map((n) => typeof window[n])")
    assert names == ["function"] * 4
    assert json.loads(demo.evaluate("JSON.stringify(window.poster.settings.mode)")) == "demo"
