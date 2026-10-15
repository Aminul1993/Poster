/** Base class of the dashboard's views: shortcuts to the app's state through the controller. */
export class View {
  /** @param {import('./controller.js').UIController} ui */
  constructor(ui) {
    this.ui = ui;
  }

  get app() { return this.ui.app; }
  get settings() { return this.ui.app.settings; }
  get store() { return this.ui.app.store; }
  get scheduler() { return this.ui.app.scheduler; }
  get feed() { return this.ui.app.feed; }
  get notifier() { return this.ui.app.notifier; }
  get validation() { return this.ui.app.validation; }
}
