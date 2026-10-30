"""The dashboard in Demo mode: every flow runs in the browser with simulated services."""

from __future__ import annotations

import json
import re
from pathlib import Path

from playwright.sync_api import Page, expect

from tests.browser.conftest import add_images, confirm, jpeg, local_input, toast


def make_room_for_tags(page: Page, free: int = 1) -> None:
    tags = page.locator("#ed-tags .tag")
    while tags.count() > 15 - free:
        tags.first.locator("button").click()


def test_upload_generate_plan_schedule_edit_delete(demo: Page) -> None:
    page = demo
    add_images(page, ("beach.jpg", jpeg((20, 120, 220))), ("forest.jpg", jpeg((30, 160, 60))),
               ("food.jpg", jpeg((220, 120, 30))))
    expect(page.locator("#gallery .thumb-card")).to_have_count(3)
    expect(page.locator("#kpi-images")).to_have_text("3 / 10")

    add_images(page, ("again.jpg", jpeg((20, 120, 220))))
    expect(page.locator("#upload-errors")).to_contain_text("Duplicate")
    expect(page.locator("#gallery .thumb-card")).to_have_count(3)

    page.click("#gen-all")
    expect(page.locator("#op-title")).to_have_text("Content generation complete")
    # The keyboard follows the work: the run's end lands on the next step, the planner's start time.
    expect(page.locator("#plan-start")).to_be_focused()
    expect(page.locator("#kpi-generated")).to_have_text("3")
    expect(page.locator("#ed-description")).not_to_have_value("")
    # Captions alone are not ready: nothing can be scheduled until each post has a time.
    expect(page.locator("#gallery .thumb-status", has_text="Needs time")).to_have_count(3)
    expect(page.locator("#kpi-needs-time")).to_have_text("3")
    expect(page.locator("#sched-all")).to_be_disabled()
    expect(page.locator("#sched-needs-time")).to_contain_text("3 posts need a publish time")
    expect(page.locator("#planner")).to_have_attribute("open", "")
    expect(page.locator("#ai-section .btn-primary")).to_have_count(1)
    expect(page.locator("#plan-all")).to_have_class(re.compile("btn-primary"))
    expect(page.locator("#upload-section")).to_have_class(re.compile("is-collapsed"))

    page.fill("#ed-description", "My own caption for this lovely picture")
    expect(page.locator("#ed-preview")).to_contain_text("My own caption for this lovely picture")
    expect(page.locator("#ed-description-count")).to_have_text("38 / 2200")

    make_room_for_tags(page)
    page.fill("#ed-tag-input", "sunny")
    page.press("#ed-tag-input", "Enter")
    expect(page.locator("#ed-tags .tag-text", has_text="#sunny")).to_have_count(1)
    # A tag that cannot be added stays in the field to be fixed, not thrown away.
    page.fill("#ed-tag-input", "!!!")
    page.press("#ed-tag-input", "Enter")
    expect(page.locator("#ed-tag-input")).to_have_value("!!!")
    page.fill("#ed-tag-input", "")

    start = local_input(2)
    page.fill("#plan-start", start)
    expect(page.locator("#plan-preview")).to_contain_text("Apply to all: 3 posts")
    page.click("#plan-all")
    toast(page, "Publish times set for 3 posts.")
    expect(page.locator("#sched-all")).to_be_focused()
    expect(page.locator("#ed-schedule")).to_have_value(start)
    expect(page.locator("#sched-all-count")).to_have_text("3")
    expect(page.locator("#sched-needs-time")).to_be_hidden()
    expect(page.locator("#sched-all")).to_have_class(re.compile("btn-primary"))
    expect(page.locator("#ai-section .btn-primary")).to_have_count(1)

    page.click("#sched-all")
    expect(page.locator("#op-title")).to_have_text("Scheduling complete")
    # One row per image: each of the 3 posts went to 2 channels.
    expect(page.locator("#sched-list > li")).to_have_count(3)
    expect(page.locator("#sched-list > li").first.locator(".sched-channel")).to_have_count(2)
    expect(page.locator("#gallery .thumb-status", has_text="Scheduled")).to_have_count(3)
    expect(page.locator("#kpi-scheduled")).to_have_text("3")
    expect(page.locator("#kpi-scheduled-label")).to_contain_text("6 channel posts")
    expect(page.locator("#op-current")).to_contain_text("3 posts queued in Demo mode (not in Buffer)")
    expect(page.locator("#op-view")).to_be_visible()
    expect(page.locator("#op-view")).to_be_focused()
    # Each row names the image it came from.
    expect(page.locator("#sched-list .sched-source", has_text="beach.jpg")).to_have_count(1)
    # A post already in Buffer is read-only in the editor until "Edit in Buffer" (read-only, not disabled).
    expect(page.locator("#ed-description")).not_to_be_editable()
    expect(page.locator("#editor")).to_have_class(re.compile("is-locked"))
    expect(page.locator("#planner")).not_to_have_attribute("open", "")
    # Every row's buttons name their post, so a list of buttons can tell the rows apart.
    expect(page.locator("#sched-list [data-action=edit]").first).to_have_attribute("aria-label", re.compile(r"^Edit post #\d · \w+\.jpg on "))

    page.evaluate("window.secondRow = document.querySelector('#sched-list > li:nth-child(2)')")
    # A post from this batch opens in the post editor, with the same fields it was written in.
    page.locator("#sched-list [data-action=edit]").first.click()
    expect(page.locator("#post-dialog")).to_be_hidden()
    expect(page.locator("#ed-description")).to_be_focused()
    expect(page.locator("#ed-description")).to_be_editable()
    page.fill("#ed-description", "Edited text for the first post")
    page.click("#ed-buffer-save")
    toast(page, "Saved in Demo mode (not in Buffer) on 2 channels.")
    # The batch mirrors Buffer: the image's card now shows the edited text.
    expect(page.locator("#gallery .thumb-caption", has_text="Edited text for the first post")).to_have_count(1)
    expect(page.locator("#sched-list .sched-text", has_text="Edited text for the first post")).to_have_count(1)
    assert page.evaluate("document.querySelector('#sched-list > li:nth-child(2)') === window.secondRow"), "unchanged rows are kept"

    page.locator("#sched-list [data-action=delete-post]").first.click()
    confirm(page, "Delete post (2 channels)")
    toast(page, "The post was deleted in Demo mode (not in Buffer).")
    expect(page.locator("#sched-list > li")).to_have_count(2)
    # The keyboard stays in the list, on the row now in the deleted one's place.
    expect(page.locator("#sched-list > li").first.locator("[data-action=edit]")).to_be_focused()
    # A post deleted on purpose waits out of "All ready" until it gets a new time, and the batch line counts it.
    expect(page.locator("#gallery .thumb-status", has_text="Removed from Buffer")).to_have_count(1)
    expect(page.locator("#kpi-removed")).to_have_text("1")
    expect(page.locator("#sched-all")).to_be_disabled()

    page.click('label:has(input[name="sched-filter"][value="published"])')
    expect(page.locator("#sched-empty-title")).to_have_text("Nothing published yet")
    page.click('label:has(input[name="sched-filter"][value="upcoming"])')
    expect(page.locator("#sched-list > li")).to_have_count(2)


def test_unicode_hashtags_survive(demo: Page) -> None:
    page = demo
    add_images(page, ("dhaka.jpg", jpeg((200, 40, 90))))
    page.click("#ed-blank-generate")
    expect(page.locator("#op-title")).to_have_text("Content generation complete")
    make_room_for_tags(page, 3)
    rally = "#র\u200d্যালি"  # the zero-width joiner picks the ra-phala form and must survive
    page.fill("#ed-tag-input", f"#বাংলাদেশ हिन्दी {rally}")
    page.press("#ed-tag-input", "Enter")
    expect(page.locator("#ed-tags .tag-text", has_text="#বাংলাদেশ")).to_have_text("#বাংলাদেশ")
    expect(page.locator("#ed-tags .tag-text", has_text="#हिन्दी")).to_have_text("#हिन्दी")
    expect(page.locator("#ed-tags .tag-text", has_text=rally)).to_have_attribute("lang", "bn")


def test_posts_without_a_time_are_caught_before_scheduling(demo: Page) -> None:
    page = demo
    add_images(page, ("one.jpg", jpeg((90, 20, 20))), ("two.jpg", jpeg((20, 90, 20))))
    page.click("#gen-all")
    expect(page.locator("#op-title")).to_have_text("Content generation complete")
    page.check("#select-all")
    page.click("#sched-selected")
    dialog = page.locator("#confirm-dialog")
    expect(dialog).to_contain_text("2 posts can't be scheduled yet")
    expect(dialog).to_contain_text("Choose a date and time to publish.")
    confirm(page, "Set times in the planner")
    expect(page.locator("#plan-start")).to_be_focused()
    expect(page.locator("#log-list")).not_to_contain_text("Not scheduled")


def test_regenerated_content_can_be_restored(demo: Page) -> None:
    page = demo
    add_images(page, ("cafe.jpg", jpeg((120, 80, 40))))
    page.click("#ed-blank-generate")
    expect(page.locator("#op-title")).to_have_text("Content generation complete")
    # Content exactly as the model wrote it is regenerated without asking; Restore still brings it back.
    page.click("#ed-generate")
    expect(page.locator("#ed-restore")).to_be_visible()
    expect(page.locator("#confirm-dialog")).to_be_hidden()
    page.fill("#ed-description", "My own words, written by hand")
    page.click("#ed-generate")
    expect(page.locator("#confirm-dialog #confirm-actions button", has_text="Cancel")).to_be_focused()
    confirm(page, "Regenerate")
    expect(page.locator("#op-title")).to_have_text("Content generation complete")
    expect(page.locator("#ed-description")).not_to_have_value("My own words, written by hand")
    page.click("#ed-restore")
    expect(page.locator("#ed-description")).to_have_value("My own words, written by hand")
    expect(page.locator("#ed-restore")).to_be_hidden()


def test_switching_to_live_asks_first(demo: Page) -> None:
    page = demo
    page.click("#mode-badge")
    expect(page.locator("#confirm-title")).to_have_text("Switch to Live mode?")
    # Going Live is the risky choice, so Cancel has the focus.
    expect(page.locator("#confirm-actions button", has_text="Cancel")).to_be_focused()
    confirm(page, "Cancel")
    expect(page.locator("#mode-badge-label")).to_have_text("Demo mode")

    # Saving Live in Settings asks the same question.
    page.click("#open-settings")
    page.click('label.mode-option:has(input[value="live"])')
    page.click("#save-settings")
    expect(page.locator("#confirm-title")).to_have_text("Switch to Live mode?")
    confirm(page, "Cancel")
    expect(page.locator("#settings-dialog")).to_be_visible()
    expect(page.locator("#mode-badge-label")).to_have_text("Demo mode")


def test_scheduled_post_is_edited_in_place(demo: Page) -> None:
    page = demo
    add_images(page, ("cafe.jpg", jpeg((120, 80, 40))))
    page.click("#ed-blank-generate")
    expect(page.locator("#op-title")).to_have_text("Content generation complete")
    page.fill("#ed-schedule", local_input(2, 9))
    page.click("#ed-schedule-post")
    expect(page.locator("#op-title")).to_have_text("Scheduling complete")
    expect(page.locator("#ed-description")).not_to_be_editable()
    expect(page.locator("#ed-result-edit")).to_have_text("Edit post")

    page.click("#ed-result-edit")
    expect(page.locator("#ed-description")).to_be_editable()
    expect(page.locator("#ed-description")).to_be_focused()
    # Only the words and the time live in Buffer; the category stays as scheduled.
    expect(page.locator("#ed-category")).not_to_be_editable()
    page.fill("#ed-description", "Morning coffee, edited in place")
    # Until it is saved, everything else still shows the post as Buffer has it.
    expect(page.locator("#gallery .thumb-caption", has_text="edited in place")).to_have_count(0)
    page.click("#ed-buffer-save")
    toast(page, "Saved in Demo mode (not in Buffer) on 2 channels.")
    expect(page.locator("#ed-description")).not_to_be_editable()
    expect(page.locator("#ed-result-edit")).to_be_focused()
    expect(page.locator("#sched-list .sched-text", has_text="Morning coffee, edited in place")).to_have_count(1)
    expect(page.locator("#gallery .thumb-caption", has_text="edited in place")).to_have_count(1)

    page.click("#ed-result-edit")
    page.fill("#ed-description", "A change I do not want")
    page.click("#ed-buffer-cancel")
    expect(page.locator("#ed-description")).to_have_value("Morning coffee, edited in place")

    # Deleted in Scheduled posts, the post says how to put it back, and the draft kept the edit.
    page.locator("#sched-list [data-action=delete-post]").first.click()
    confirm(page, "Delete post")
    expect(page.locator("#ed-result")).to_contain_text("Schedule post puts it back at")
    expect(page.locator("#ed-description")).to_have_value("Morning coffee, edited in place")


def test_planner_fits_around_times_already_taken(demo: Page) -> None:
    page = demo
    add_images(page, ("one.jpg", jpeg((90, 20, 20))), ("two.jpg", jpeg((20, 90, 20))), ("three.jpg", jpeg((20, 20, 90))))
    page.click("#gen-all")
    expect(page.locator("#op-title")).to_have_text("Content generation complete")
    start = local_input(2, 9)
    page.locator("#gallery .thumb-card").first.locator("[data-action=activate]").click()
    page.fill("#ed-schedule", start)
    page.fill("#plan-start", start)
    expect(page.locator("#plan-preview")).to_contain_text("Apply to all: 2 posts")
    expect(page.locator("#plan-preview")).to_contain_text("Skips 1 time other posts already have.")
    page.click("#plan-all")
    page.locator("#gallery .thumb-card").nth(1).locator("[data-action=activate]").click()
    expect(page.locator("#ed-schedule")).not_to_have_value(start)


def test_list_view_edits_captions_and_times_in_place(demo: Page) -> None:
    page = demo
    add_images(page, ("one.jpg", jpeg((90, 20, 20))), ("two.jpg", jpeg((20, 90, 20))))
    page.click("#gen-all")
    expect(page.locator("#op-title")).to_have_text("Content generation complete")
    page.click('label:has(input[name="gallery-view"][value="list"])')
    expect(page.locator("#gallery")).to_have_class(re.compile("is-list"))
    second = page.locator("#gallery .thumb-card").nth(1)
    second.locator(".list-caption").fill("Hand-written caption for the second photo")
    second.locator(".list-time").fill(local_input(2))
    expect(second.locator(".list-status")).to_have_text("Ready")
    second.locator("[data-action=activate]").click()
    expect(page.locator("#ed-description")).to_have_value("Hand-written caption for the second photo")
    page.reload()
    expect(page.locator("#gallery")).to_have_class(re.compile("is-list"))


def test_gallery_management(demo: Page) -> None:
    page = demo
    add_images(page, ("a.jpg", jpeg((10, 10, 200))), ("b.jpg", jpeg((10, 200, 10))), ("c.jpg", jpeg((200, 10, 10))))
    names = page.locator("#gallery .thumb-name")
    expect(names).to_have_text(["a.jpg", "b.jpg", "c.jpg"])
    page.locator("#gallery .thumb-card").first.locator("[data-action=move-right]").click()
    expect(names).to_have_text(["b.jpg", "a.jpg", "c.jpg"])
    page.locator("#gallery .thumb-card").nth(2).locator("[data-action=move-left]").click()
    expect(names).to_have_text(["b.jpg", "c.jpg", "a.jpg"])

    page.check("#select-all")
    expect(page.locator("#selection-info")).to_have_text("3 of 3 selected")
    expect(page.locator("#gen-selected-count")).to_have_text("3")

    page.locator("#gallery .thumb-card").nth(1).locator("[data-action=activate]").click()
    expect(page.locator("#ed-name")).to_have_text("c.jpg")
    page.click("#ed-next")
    expect(page.locator("#ed-name")).to_have_text("a.jpg")

    page.locator("#gallery .thumb-card").first.locator("[data-action=remove]").click()
    toast(page, 'Removed "b.jpg".')
    expect(names).to_have_text(["c.jpg", "a.jpg"])
    # Undo puts the image back where it was.
    page.locator("#toast-region .toast", has_text='Removed "b.jpg".').locator(".toast-action").click()
    expect(names).to_have_text(["b.jpg", "c.jpg", "a.jpg"])
    page.locator("#gallery .thumb-card").first.locator("[data-action=remove]").click()
    expect(names).to_have_text(["c.jpg", "a.jpg"])

    page.click("#clear-images")
    confirm(page, "Remove all")
    expect(page.locator("#gallery-empty")).to_be_visible()
    expect(page.locator("#kpi-images")).to_have_text("0 / 10")


def test_generated_image_joins_the_gallery(demo: Page) -> None:
    page = demo
    page.click('label:has(input[name="image-source"][value="generate"])')
    expect(page.locator("#source-generate")).to_be_visible()
    page.fill("#imggen-prompt", "A cosy coffee shop at sunrise")
    expect(page.locator("#imggen-count")).to_have_text("29 / 1000")
    page.click("#imggen-button")
    expect(page.locator("#gallery .thumb-name")).to_have_text(["ai-a-cosy-coffee-shop-at-sunrise.jpg"])


def test_edited_image_joins_the_gallery(demo: Page) -> None:
    page = demo
    page.click('label:has(input[name="image-source"][value="edit"])')
    expect(page.locator("#source-edit")).to_be_visible()
    expect(page.locator("#upload-sub")).to_contain_text("describe a change")
    page.click("#imgedit-button")
    expect(page.locator("#imgedit-error")).to_have_text("Add the photo to change first.")

    page.set_input_files("#imgedit-input", files=[{"name": "harbour.jpg", "mimeType": "image/jpeg",
                                                    "buffer": jpeg((40, 90, 160))}])
    expect(page.locator("#imgedit-source-name")).to_have_text("harbour.jpg")
    expect(page.locator("#imgedit-source-detail")).to_contain_text("320×240")
    expect(page.locator("#imgedit-thumb img")).to_be_visible()
    expect(page.locator("#imgedit-dropzone")).to_be_hidden()
    expect(page.locator("#imgedit-error")).to_be_hidden()

    page.click("#imgedit-button")
    expect(page.locator("#imgedit-error")).to_have_text("Describe the change in a few words first.")
    page.fill("#imgedit-prompt", "Make it a watercolour painting")
    expect(page.locator("#imgedit-count")).to_have_text("30 / 1000")
    page.click("#imgedit-button")
    # Only the updated copy joins the gallery; the photo stays ready for another try.
    expect(page.locator("#gallery .thumb-name")).to_have_text(["ai-edit-make-it-a-watercolour-painting.jpg"])
    expect(page.locator("#imgedit-source-name")).to_have_text("harbour.jpg")
    page.click("#imgedit-button")
    expect(page.locator("#gallery .thumb-card")).to_have_count(2)

    page.click("#imgedit-remove")
    expect(page.locator("#imgedit-dropzone")).to_be_visible()
    page.reload()
    expect(page.locator("#source-edit")).to_be_visible()


def test_settings_save_export_import(demo: Page, tmp_path: Path) -> None:
    page = demo
    page.click("#open-settings")
    expect(page.locator("#settings-dialog")).to_be_visible()
    page.select_option("#set-tone", "bold")
    page.fill("#set-desc-max", "300")
    page.click("#save-settings")
    expect(page.locator("#settings-dialog")).to_be_hidden()
    toast(page, "Settings saved.")
    expect(page.locator("#ai-sub")).to_contain_text("Bold tone")

    page.click("#open-settings")
    with page.expect_download() as download:
        page.click("#export-config")
    exported = json.loads(Path(download.value.path()).read_text(encoding="utf-8"))
    assert exported["app"] == "Poster" and exported["settings"]["tone"] == "bold" and exported["settings"]["descMax"] == 300

    exported["settings"]["tone"] = "luxury"
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(exported), encoding="utf-8")
    page.set_input_files("#import-input", str(path))
    # While the drawer is open, what it did is said in its footer (a toast would sit under its backdrop).
    expect(page.locator("#settings-dirty")).to_contain_text('Settings imported from "settings.json"')
    expect(page.locator("#set-tone")).to_have_value("luxury")
    page.click("#cancel-settings")
    expect(page.locator("#settings-dialog")).to_be_hidden()
    expect(page.locator("#ai-sub")).to_contain_text("Luxury tone")

    page.click("#open-settings")
    page.fill("#set-desc-min", "")
    page.click("#save-settings")
    expect(page.locator("#settings-errors")).to_be_visible()
    page.click("#cancel-settings")
    confirm(page, "Discard")
    expect(page.locator("#settings-dialog")).to_be_hidden()


def test_reset_all_settings(demo: Page, problems: list[str]) -> None:
    page = demo
    page.click("#theme-toggle")
    page.click('label:has(input[name="image-source"][value="generate"])')
    page.click("#open-settings")
    page.click("#reset-all")
    confirm(page, "Reset all settings")
    expect(page.locator("#settings-dirty")).to_contain_text("All settings were reset to the server defaults.")
    errors = list(problems)
    problems.clear()
    assert not errors
    expect(page.locator("#source-upload")).to_be_visible()


def test_theme_and_activity_log(demo: Page) -> None:
    page = demo
    before = page.get_attribute("html", "data-theme")
    page.click("#theme-toggle")
    after = page.get_attribute("html", "data-theme")
    assert {before, after} == {"light", "dark"}
    page.reload()
    expect(page.locator("html")).to_have_attribute("data-theme", after)

    expect(page.locator("#log-list > li").first).to_be_visible()
    page.select_option("#log-filter", "error")
    expect(page.locator("#log-empty-title")).to_have_text("No matching activity")
    page.select_option("#log-filter", "all")
    page.click("#clear-log")
    confirm(page, "Clear log")
    expect(page.locator("#log-count")).to_have_text("0")


def test_caption_language_for_the_batch_and_one_post(demo: Page) -> None:
    page = demo
    page.click("#open-settings")
    page.select_option("#set-language", "hi")
    page.click("#save-settings")
    expect(page.locator("#ai-sub")).to_contain_text("Hindi")
    add_images(page, ("market.jpg", jpeg((150, 60, 20))), ("ghat.jpg", jpeg((20, 60, 150))))
    expect(page.locator("#ed-language option").first).to_have_text("Batch setting (Hindi)")
    # One post asks for its own language; the other follows the batch.
    page.select_option("#ed-language", "bn-en")
    page.click("#gen-all")
    expect(page.locator("#op-title")).to_have_text("Content generation complete")
    expect(page.locator("#ed-description")).to_have_value(re.compile("[ঀ-৿]"))
    expect(page.locator("#ed-description")).to_have_attribute("lang", "bn")
    page.click("#ed-next")
    expect(page.locator("#ed-language")).to_have_value("")
    expect(page.locator("#ed-description")).to_have_value(re.compile("[ऀ-ॿ]"))


def test_edit_selected_hashtags_and_category_with_undo(demo: Page) -> None:
    page = demo
    add_images(page, ("one.jpg", jpeg((90, 20, 20))), ("two.jpg", jpeg((20, 90, 20))), ("three.jpg", jpeg((20, 20, 90))))
    page.click("#gen-all")
    expect(page.locator("#op-title")).to_have_text("Content generation complete")
    expect(page.locator("#bulk-edit")).to_be_hidden()
    page.check("#select-all")
    page.click("#bulk-edit")
    expect(page.locator("#bulk-title")).to_have_text("Edit 3 selected posts")
    expect(page.locator("#bulk-add")).to_be_focused()
    page.click("#bulk-apply")
    expect(page.locator("#bulk-error")).to_have_text("Add a hashtag, tick one to remove, or enter a category.")
    first_tag = page.locator("#bulk-remove input").first.get_attribute("value")
    page.locator("#bulk-remove .bulk-tag").first.click()
    page.fill("#bulk-add", "monsoon #ঢাকা")
    page.fill("#bulk-category", "Street")
    page.click("#bulk-apply")
    expect(page.locator("#bulk-dialog")).to_be_hidden()
    toast(page, "Updated 3 posts: added #monsoon #ঢাকা")
    expect(page.locator("#ed-tags .tag-text", has_text="#monsoon")).to_have_count(1)
    expect(page.locator("#ed-tags .tag-text").filter(has_text=re.compile(f"^{re.escape(first_tag)}$"))).to_have_count(0)
    expect(page.locator("#ed-category")).to_have_value("Street")
    page.locator("#toast-region .toast", has_text="Updated 3 posts").locator(".toast-action").click()
    expect(page.locator("#ed-tags .tag-text", has_text="#monsoon")).to_have_count(0)
    expect(page.locator("#ed-category")).not_to_have_value("Street")


def test_next_step_shortcut_and_shortcut_list(demo: Page) -> None:
    page = demo
    add_images(page, ("one.jpg", jpeg((90, 20, 20))), ("two.jpg", jpeg((20, 90, 20))))
    expect(page.locator("#gen-all")).to_have_attribute("aria-keyshortcuts", "Control+Shift+Enter")
    page.locator("body").focus()
    page.keyboard.press("Control+Shift+Enter")
    expect(page.locator("#op-title")).to_have_text("Content generation complete")
    # Next, from the planner's start time where the run's end put the cursor: Apply to all...
    expect(page.locator("#plan-start")).to_be_focused()
    expect(page.locator("#plan-all")).to_have_attribute("aria-keyshortcuts", "Control+Shift+Enter")
    page.keyboard.press("Control+Shift+Enter")
    toast(page, "Publish times set for 2 posts.")
    # ...then All ready schedules them.
    page.keyboard.press("Control+Shift+Enter")
    expect(page.locator("#op-title")).to_have_text("Scheduling complete")
    # "?" is just a character in a field; elsewhere it lists the shortcuts.
    page.click('label:has(input[name="gallery-view"][value="list"])')
    page.locator("#gallery .list-caption").first.focus()
    page.keyboard.press("?")
    expect(page.locator("#shortcuts-dialog")).to_be_hidden()
    page.evaluate("document.activeElement.blur()")
    page.keyboard.press("?")
    expect(page.locator("#shortcuts-dialog")).to_be_visible()
    expect(page.locator("#shortcuts-close")).to_be_focused()
    page.keyboard.press("Escape")
    expect(page.locator("#shortcuts-dialog")).to_be_hidden()
