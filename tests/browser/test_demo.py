"""The dashboard in Demo mode: every flow runs in the browser with simulated services."""

from __future__ import annotations

import json
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
    expect(page.locator("#kpi-images")).to_have_text("3 / 25")

    add_images(page, ("again.jpg", jpeg((20, 120, 220))))
    expect(page.locator("#upload-errors")).to_contain_text("Duplicate")
    expect(page.locator("#gallery .thumb-card")).to_have_count(3)

    page.click("#gen-all")
    expect(page.locator("#op-title")).to_have_text("Content generation complete")
    expect(page.locator("#kpi-generated")).to_have_text("3")
    expect(page.locator("#ed-description")).not_to_have_value("")

    page.fill("#ed-description", "My own caption for this lovely picture")
    expect(page.locator("#ed-preview")).to_contain_text("My own caption for this lovely picture")
    expect(page.locator("#ed-description-count")).to_have_text("38 / 2200")

    make_room_for_tags(page)
    page.fill("#ed-tag-input", "sunny")
    page.press("#ed-tag-input", "Enter")
    expect(page.locator("#ed-tags .tag-text", has_text="#sunny")).to_have_count(1)

    page.click("#planner summary")
    start = local_input(2)
    page.fill("#plan-start", start)
    page.click("#plan-all")
    toast(page, "Publish times set for 3 posts.")
    expect(page.locator("#ed-schedule")).to_have_value(start)

    page.click("#sched-all")
    expect(page.locator("#op-title")).to_have_text("Scheduling complete")
    expect(page.locator("#sched-list > li")).to_have_count(6)
    expect(page.locator("#gallery .thumb-status", has_text="Scheduled")).to_have_count(3)
    expect(page.locator("#kpi-scheduled")).to_have_text("6")

    page.evaluate("window.secondRow = document.querySelector('#sched-list > li:nth-child(2)')")
    page.locator("#sched-list [data-action=edit]").first.click()
    expect(page.locator("#post-dialog")).to_be_visible()
    page.fill("#post-text", "Edited text for the first post")
    page.click("#post-save")
    toast(page, "The post was updated in Buffer.")
    expect(page.locator("#sched-list .sched-text", has_text="Edited text for the first post")).to_have_count(1)
    assert page.evaluate("document.querySelector('#sched-list > li:nth-child(2)') === window.secondRow"), "unchanged rows are kept"

    page.locator("#sched-list [data-action=delete-post]").first.click()
    confirm(page, "Delete post")
    toast(page, "The post was deleted from Buffer.")
    expect(page.locator("#sched-list > li")).to_have_count(5)

    page.click('label:has(input[name="sched-filter"][value="published"])')
    expect(page.locator("#sched-empty-title")).to_have_text("Nothing published yet")
    page.click('label:has(input[name="sched-filter"][value="upcoming"])')
    expect(page.locator("#sched-list > li")).to_have_count(5)


def test_unicode_hashtags_survive(demo: Page) -> None:
    page = demo
    add_images(page, ("dhaka.jpg", jpeg((200, 40, 90))))
    page.click("#gen-active")
    expect(page.locator("#op-title")).to_have_text("Content generation complete")
    make_room_for_tags(page, 2)
    page.fill("#ed-tag-input", "#বাংলাদেশ हिन्दी")
    page.press("#ed-tag-input", "Enter")
    expect(page.locator("#ed-tags .tag-text", has_text="#বাংলাদেশ")).to_have_text("#বাংলাদেশ")
    expect(page.locator("#ed-tags .tag-text", has_text="#हिन्दी")).to_have_text("#हिन्दी")


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

    page.click("#clear-images")
    confirm(page, "Remove all")
    expect(page.locator("#gallery-empty")).to_be_visible()
    expect(page.locator("#kpi-images")).to_have_text("0 / 25")


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
    toast(page, 'Settings imported from "settings.json"')
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
    toast(page, "All settings were reset to the server defaults.")
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
