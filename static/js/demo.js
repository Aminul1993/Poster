/** Demo mode: simulated AI, image generation and Buffer, with no network. */
import { SERVER } from './config.js';
import { ApiError, sleep } from './errors.js';
import { canvasToBlob, decodeImage, drawScaled, hashString } from './image.js';
import { SettingsModel } from './settings.js';
import { clamp, truncate, uid } from './util.js';
import { BufferService, POST_FILTERS, normalizePost, normalizePostResults, sortPosts } from './services/buffer.js';
import { ImageGenService, generatedImageFile } from './services/imagegen.js';
import { OllamaService } from './services/ollama.js';

export const DEMO_LIBRARY = Object.freeze({
  Travel: {
    words: ['travel', 'trip', 'beach', 'sea', 'ocean', 'mountain', 'mountains', 'island', 'sunset', 'sunrise', 'vacation', 'holiday', 'hike', 'lake', 'road', 'journey', 'coast', 'desert', 'paris', 'tokyo', 'bali', 'rome', 'london', 'waves', 'harbor'],
    tags: ['#travel', '#wanderlust', '#travelgram', '#explore', '#adventure', '#vacation', '#travelphotography', '#bucketlist', '#getaway', '#instatravel', '#exploremore', '#roamtheplanet', '#weekendgetaway', '#travelinspiration'],
    captions: ['Some places stay with you long after you leave.', 'Found a corner of the world worth slowing down for.', 'Proof that the best views come after the longest roads.'],
    alts: ['Saving this view for every Monday that needs it.', 'Where would you escape to if you could leave today?', 'Less planning, more wandering. Who is coming along?']
  },
  Food: {
    words: ['food', 'pizza', 'burger', 'coffee', 'cake', 'dinner', 'lunch', 'breakfast', 'brunch', 'dessert', 'meal', 'recipe', 'cafe', 'restaurant', 'sushi', 'salad', 'pasta', 'bread', 'tea', 'cookie', 'chocolate', 'latte', 'kitchen'],
    tags: ['#foodie', '#foodphotography', '#instafood', '#yummy', '#foodlover', '#delicious', '#homemade', '#foodstagram', '#eeeeeats', '#tasty', '#foodgasm', '#cheflife', '#goodfood', '#eatwell'],
    captions: ['Good food, good mood. It really is that simple.', 'Made with patience and best enjoyed slowly.', 'This plate deserves a moment of silence.'],
    alts: ['Tag the person you would share this with (or not).', 'Comfort food that tastes like a long weekend.', 'One bite in and every plan for the day changed.']
  },
  Nature: {
    words: ['nature', 'flower', 'flowers', 'forest', 'tree', 'trees', 'garden', 'leaf', 'leaves', 'plant', 'plants', 'park', 'sky', 'clouds', 'river', 'waterfall', 'meadow', 'spring', 'autumn', 'snow', 'bloom'],
    tags: ['#nature', '#naturephotography', '#naturelovers', '#outdoors', '#earthfocus', '#greenery', '#landscape', '#wildernessculture', '#natureaddict', '#fresh', '#getoutside', '#botanical', '#seasons', '#peaceful'],
    captions: ['Nature never rushes, yet everything gets done.', 'A quiet reminder to step outside more often.', 'Fresh air, clear mind, full heart.'],
    alts: ['Take a breath. This one is your daily reset.', 'The kind of calm you cannot download.', 'Sometimes the best plan is no plan, just this.']
  },
  Pets: {
    words: ['dog', 'dogs', 'cat', 'cats', 'puppy', 'kitten', 'pet', 'pets', 'doggo', 'pup', 'kitty', 'paws', 'bunny', 'hamster'],
    tags: ['#petsofinstagram', '#dogsofinstagram', '#catsofinstagram', '#cute', '#petlovers', '#adorable', '#furbaby', '#instapets', '#animallovers', '#pawsome', '#bestfriend', '#weeklyfluff', '#petphotography', '#love'],
    captions: ['Professional napper, part-time heart thief.', 'Unconditional love comes with four paws.', 'This face could get away with anything.'],
    alts: ['Rate this cutie from 10 to 10.', 'Our favourite coworker reporting for duty.', 'Everything is better with a furry sidekick.']
  },
  Fashion: {
    words: ['fashion', 'style', 'outfit', 'dress', 'shoes', 'sneakers', 'bag', 'model', 'look', 'ootd', 'jacket', 'jewelry', 'watch', 'wear', 'boutique'],
    tags: ['#fashion', '#style', '#ootd', '#outfitinspo', '#fashionista', '#streetstyle', '#lookbook', '#instafashion', '#styleinspo', '#whatiwore', '#fashionblogger', '#trendy', '#wardrobe', '#effortless'],
    captions: ['Style is a way to say who you are without speaking.', 'Dressed for the life you are building.', 'Confidence is the best accessory, but this helps.'],
    alts: ['Would you wear this? Be honest.', 'Saving this look for the next big day.', 'Simple pieces, strong statement.']
  },
  Fitness: {
    words: ['gym', 'run', 'running', 'yoga', 'workout', 'fitness', 'sport', 'sports', 'training', 'bike', 'cycling', 'swim', 'marathon', 'football', 'tennis'],
    tags: ['#fitness', '#workout', '#fitnessmotivation', '#healthylifestyle', '#training', '#gymlife', '#fitfam', '#strong', '#wellness', '#motivation', '#active', '#movemore', '#nevergiveup', '#fitspo'],
    captions: ['Discipline today, results tomorrow.', 'Every rep counts, especially the hard ones.', 'Strong is built one choice at a time.'],
    alts: ['Show up for yourself. Nobody else can do it for you.', 'Small steps, every single day.', 'Your only competition is who you were yesterday.']
  },
  Technology: {
    words: ['laptop', 'phone', 'tech', 'code', 'coding', 'computer', 'desk', 'setup', 'gadget', 'keyboard', 'screen', 'app', 'robot', 'camera', 'drone', 'office', 'startup'],
    tags: ['#tech', '#technology', '#innovation', '#workspace', '#desksetup', '#productivity', '#gadgets', '#startup', '#developer', '#digital', '#future', '#techlife', '#remotework', '#design'],
    captions: ['Where ideas turn into something real.', 'Built for focus, designed for flow.', 'The tools change. The drive to build does not.'],
    alts: ['What does your ideal setup look like?', 'Productivity mode: on.', 'Clean setup, clear mind, big plans.']
  },
  Architecture: {
    words: ['building', 'buildings', 'architecture', 'house', 'home', 'interior', 'room', 'tower', 'bridge', 'street', 'skyline', 'church', 'window', 'stairs', 'urban'],
    tags: ['#architecture', '#design', '#interiordesign', '#architecturephotography', '#urban', '#cityscape', '#archilovers', '#homedecor', '#lines', '#minimalism', '#buildings', '#structure', '#spaces', '#perspective'],
    captions: ['Lines, light and a little bit of magic.', 'Every space tells a story if you look closely.', 'Design that makes you stop and look up.'],
    alts: ['Geometry has never looked this good.', 'Which detail caught your eye first?', 'Built to last, designed to impress.']
  },
  Art: {
    words: ['art', 'painting', 'drawing', 'design', 'illustration', 'sketch', 'mural', 'gallery', 'museum', 'craft', 'pottery', 'canvas', 'graffiti', 'abstract'],
    tags: ['#art', '#artist', '#artwork', '#creative', '#instaart', '#artoftheday', '#contemporaryart', '#illustration', '#artistsoninstagram', '#design', '#makersgonnamake', '#inspiration', '#color', '#visualart'],
    captions: ['Creativity takes courage. This took a lot of it.', 'Made by hand, meant for the heart.', 'Colour first, questions later.'],
    alts: ['What do you see first? Tell us below.', 'A small piece of a big imagination.', 'Art is how we decorate time.']
  },
  Lifestyle: {
    words: [],
    tags: ['#lifestyle', '#everydaymoments', '#goodvibes', '#dailyinspiration', '#livethelittlethings', '#mood', '#aesthetic', '#instadaily', '#weekend', '#selfcare', '#simplepleasures', '#vibes', '#photooftheday', '#happiness'],
    captions: ['Collecting moments, not things.', 'The little things are never really little.', 'Today looked a lot like this, and that was perfect.'],
    alts: ['Here is your sign to slow down today.', 'Find joy in the ordinary.', 'Small moments, big feelings.']
  }
});

export const DEMO_COLOR_LINES = Object.freeze({
  blue: ['All those calm blue tones.', '#bluetones'], teal: ['Cool teal tones all around.', '#tealandorange'],
  green: ['Fresh greens everywhere you look.', '#greenery'], golden: ['Golden light, slow moments.', '#goldenhour'],
  orange: ['Warm glow included.', '#warmtones'], red: ['Bold reds that refuse to be ignored.', '#redaesthetic'],
  pink: ['Soft pinks for a softer day.', '#pinkaesthetic'], purple: ['A little purple magic.', '#purplevibes'],
  dark: ['Moody, low light and full of character.', '#moodygrams'], bright: ['Clean, bright and airy.', '#minimal'],
  neutral: ['Simple tones, nothing extra.', '#neutraltones']
});

export const DEMO_TONE_ENDINGS = Object.freeze({
  friendly: ' What do you think?', professional: '', playful: ' ✨', inspirational: ' Keep chasing what lights you up.',
  luxury: ' Crafted for those who notice the details.', bold: ' Do not scroll past this one.'
});

/** Builds plausible content from the file name and colour statistics. */
export const DemoContentGenerator = {
  stopWords: new Set(['img', 'dsc', 'dscn', 'dscf', 'jpg', 'jpeg', 'png', 'webp', 'photo', 'image', 'pic', 'copy', 'final', 'edit', 'edited',
    'screenshot', 'pxl', 'whatsapp', 'untitled', 'camera', 'new', 'file', 'scaled', 'export', 'mvimg', 'imgp', 'and', 'the', 'with']),

  /** @param {string} name @returns {string[]} meaningful words from a file name */
  tokens(name) {
    const base = name.replace(/\.[^.]+$/, '');
    return (base.toLowerCase().match(/[a-z]{3,}/g) || []).filter((w) => !this.stopWords.has(w));
  },

  /** @param {object} item @param {object} settings @returns {object} raw model-like JSON */
  generate(item, settings) {
    const seed = hashString(item.fingerprint || item.name);
    const words = this.tokens(item.name);
    const stats = item.stats || { colorName: 'neutral', saturation: 0.3, brightness: 0.5 };
    let category = Object.keys(DEMO_LIBRARY).find((key) => DEMO_LIBRARY[key].words.some((w) => words.includes(w)));
    if (!category) {
      category = { blue: 'Travel', teal: 'Travel', green: 'Nature', golden: 'Lifestyle', orange: 'Food', red: 'Fashion', pink: 'Fashion', purple: 'Art', dark: 'Art', bright: 'Lifestyle', neutral: 'Lifestyle' }[stats.colorName] || 'Lifestyle';
    }
    const lib = DEMO_LIBRARY[category];
    const [colorLine, colorTag] = DEMO_COLOR_LINES[stats.colorName] || DEMO_COLOR_LINES.neutral;
    const ending = DEMO_TONE_ENDINGS[settings.tone] ?? '';
    const description = `${lib.captions[seed % lib.captions.length]} ${colorLine}${ending}`.trim();
    const alternative = lib.alts[(seed >>> 3) % lib.alts.length];
    const rotated = [...lib.tags.slice(seed % lib.tags.length), ...lib.tags.slice(0, seed % lib.tags.length)];
    const hashtags = [...words.slice(0, 3).map((w) => `#${w}`), colorTag, ...rotated].slice(0, 10 + (seed % 6));
    const bonus = { Pets: 8, Food: 5, Travel: 4, Fashion: 3 }[category] || 0;
    const balance = stats.brightness > 0.3 && stats.brightness < 0.8 ? 6 : 0;
    const score = clamp(Math.round(54 + stats.saturation * 24 + balance + bonus + ((seed >>> 5) % 17) - 8), 28, 97);
    return { description, alternative_description: alternative, hashtags, category, engagement_score: score };
  }
};

/** Demo-mode OllamaService: same interface, simulated latency, no network. */
export class DemoOllamaService extends OllamaService {
  get isDemo() { return true; }

  /** @returns {Promise<{models: string[], modelAvailable: boolean, host: string}>} */
  async testConnection({ settings, signal } = {}) {
    const s = settings ?? this.getSettings();
    await sleep(350, signal);
    return { models: [`${s.model} (simulated)`], modelAvailable: true, host: 'demo' };
  }

  /** @returns {Promise<{content: object, issues: string[], model: string, repaired: boolean}>} */
  async generateContent(item, { signal, settings } = {}) {
    const s = settings ?? this.getSettings();
    await sleep(650 + (hashString(item.fingerprint || item.name) % 1100), signal);
    const raw = DemoContentGenerator.generate(item, s);
    return { ...this.validation.normalizeGeneratedContent(raw, s), model: `${s.model} (demo)`, repaired: false };
  }
}

/** Demo-mode ImageGenService: draws a placeholder in the browser, no network. */
export class DemoImageGenService extends ImageGenService {
  get isDemo() { return true; }

  get available() { return true; }

  /** @returns {Promise<File>} a gradient with the description written on it */
  async generateImage(prompt, aspect, { signal } = {}) {
    await sleep(900 + (hashString(prompt) % 900), signal);
    const [width, height] = SERVER.imageGen.aspects[aspect] || [1024, 1024];
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    // Random colours and circles, so every run gives a new image (identical files are rejected as duplicates).
    const hue = Math.floor(Math.random() * 360);
    const gradient = ctx.createLinearGradient(0, 0, width, height);
    gradient.addColorStop(0, `hsl(${hue}, 70%, 58%)`);
    gradient.addColorStop(1, `hsl(${(hue + 50) % 360}, 65%, 32%)`);
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, width, height);
    for (let i = 0; i < 14; i += 1) {
      ctx.fillStyle = `hsla(${(hue + 30 + Math.random() * 60) % 360}, 80%, 75%, ${0.08 + Math.random() * 0.14})`;
      ctx.beginPath();
      ctx.arc(Math.random() * width, Math.random() * height, (0.05 + Math.random() * 0.2) * Math.min(width, height), 0, Math.PI * 2);
      ctx.fill();
    }
    const size = Math.round(Math.min(width, height) / 16);
    ctx.fillStyle = 'rgba(255, 255, 255, .94)';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.font = `600 ${size}px system-ui, sans-serif`;
    const lines = DemoImageGenService.wrap(ctx, truncate(prompt, 140), width * 0.8);
    lines.forEach((line, index) => ctx.fillText(line, width / 2, height / 2 + (index - (lines.length - 1) / 2) * size * 1.3));
    ctx.font = `500 ${Math.round(size * 0.5)}px system-ui, sans-serif`;
    ctx.fillText('Demo image · not AI-generated', width / 2, height - size * 1.4);
    const blob = await canvasToBlob(canvas, 'image/jpeg', 0.9);
    if (!blob) throw new ApiError('The demo image could not be drawn.', { kind: 'client', service: 'Pollinations' });
    return generatedImageFile(blob, prompt, 'image/jpeg');
  }

  /** @returns {Promise<File>} the photo in a random colour cast, with the description written on it */
  async editImage(file, prompt, { signal } = {}) {
    await sleep(900 + (hashString(prompt) % 900), signal);
    const source = await decodeImage(file);
    let canvas;
    try {
      canvas = drawScaled(source, 1024);
    } finally {
      if (typeof source.close === 'function') source.close();
    }
    const { width, height } = canvas;
    const ctx = canvas.getContext('2d');
    // A random cast, so every run gives a new image (identical files are rejected as duplicates).
    ctx.globalCompositeOperation = 'color';
    ctx.fillStyle = `hsla(${Math.floor(Math.random() * 360)}, 75%, 50%, .45)`;
    ctx.fillRect(0, 0, width, height);
    ctx.globalCompositeOperation = 'source-over';
    const size = Math.max(12, Math.round(Math.min(width, height) / 22));
    ctx.font = `600 ${size}px system-ui, sans-serif`;
    const lines = DemoImageGenService.wrap(ctx, truncate(prompt, 140), width * 0.86);
    const band = (lines.length + 1.6) * size * 1.3;
    ctx.fillStyle = 'rgba(0, 0, 0, .55)';
    ctx.fillRect(0, height - band, width, band);
    ctx.fillStyle = 'rgba(255, 255, 255, .94)';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    lines.forEach((line, index) => ctx.fillText(line, width / 2, height - band + (index + 0.8) * size * 1.3));
    ctx.font = `500 ${Math.round(size * 0.6)}px system-ui, sans-serif`;
    ctx.fillText('Demo edit · not AI-generated', width / 2, height - size * 0.9);
    const blob = await canvasToBlob(canvas, 'image/jpeg', 0.9);
    if (!blob) throw new ApiError('The demo image could not be drawn.', { kind: 'client', service: 'Pollinations' });
    return generatedImageFile(blob, prompt, 'image/jpeg', 'ai-edit');
  }

  /** Splits text into lines that fit `maxWidth` in the context's current font. */
  static wrap(ctx, text, maxWidth) {
    const lines = [];
    let line = '';
    for (const word of text.split(' ')) {
      const next = line ? `${line} ${word}` : word;
      if (line && ctx.measureText(next).width > maxWidth) {
        lines.push(line);
        line = word;
      } else {
        line = next;
      }
    }
    if (line) lines.push(line);
    return lines;
  }
}

/** Demo-mode BufferService: sample channels, simulated latency, fake post IDs. */
export class DemoBufferService extends BufferService {
  constructor(deps) {
    super(deps);
    /** Simulated Buffer posts, in memory only (gone when the tab closes). */
    this.posts = [];
  }

  get isDemo() { return true; }

  getConnection() {
    return {
      account: { id: 'demo-account', email: 'demo@poster.app', organizations: [{ id: 'demo-org', name: 'Demo workspace' }] },
      channels: SettingsModel.demoChannels.map((c) => ({ ...c })),
      connectedAt: new Date().toISOString(),
      tokenId: ''
    };
  }

  saveConnection() { /* nothing to cache in Demo mode */ }

  selectedChannelIds(settings = this.getSettings()) {
    return [...(settings.channelIds?.demo || [])];
  }

  assertConfigured() { /* Demo mode needs no credentials */ }

  async validateBufferToken({ signal } = {}) {
    await sleep(300, signal);
    return this.getConnection().account;
  }

  async connectToBuffer({ signal } = {}) {
    await sleep(500, signal);
    const { account, channels } = this.getConnection();
    return { account, channels };
  }

  async uploadMedia(item, { signal } = {}) {
    const manual = String(item.mediaUrl || '').trim();
    if (manual) {
      const error = this.validation.validateMediaUrl(manual);
      if (error) throw new ApiError(`"${item.name}": ${error}`, { kind: 'validation', service: 'Buffer' });
      return { url: manual, thumbnailUrl: null };
    }
    if (item.uploadedUrl) return { url: item.uploadedUrl, thumbnailUrl: null };
    await sleep(250, signal);
    return { url: `https://media.poster-demo.invalid/${(item.fingerprint || uid('img')).replace(/[^a-z0-9]/gi, '').slice(-20)}.jpg`, thumbnailUrl: null };
  }

  async schedulePost(post) {
    if (!Array.isArray(post.channelIds) || !post.channelIds.length) {
      throw new ApiError('No demo channels selected. Choose at least one in Settings → Buffer.', { kind: 'config', service: 'Buffer' });
    }
    if (!(post.dueAt instanceof Date) || post.dueAt.getTime() <= Date.now()) {
      throw new ApiError('The publish time must be a valid future date.', { kind: 'validation', service: 'Buffer' });
    }
    const posts = [];
    for (const channelId of post.channelIds) {
      await sleep(220 + (hashString(channelId + post.text) % 300));
      const created = { channelId, postId: uid('demo').replace('_', '-'), dueAt: post.dueAt.toISOString(), status: 'scheduled' };
      posts.push(created);
      const channel = this.channels().find((c) => c.id === channelId);
      this.posts.push({
        ...created, text: post.text, createdAt: new Date().toISOString(), channelName: channel?.name || channelId,
        service: channel?.service || '', thumbnail: post.thumbnailUrl || null
      });
    }
    return { posts, failures: [] };
  }

  /** Demo posts count as published once their publish time has passed. */
  #current() {
    const now = Date.now();
    return this.posts.map((p) => {
      const sent = new Date(p.dueAt).getTime() <= now;
      return normalizePost({ ...p, status: sent ? 'sent' : p.status, sentAt: sent ? p.dueAt : null });
    });
  }

  async listPosts({ filter }) {
    await sleep(250);
    const statuses = POST_FILTERS[filter];
    return { posts: sortPosts(this.#current().filter((p) => !statuses || statuses.includes(p.status)), filter), next: null };
  }

  async editPosts(postIds, { text, dueAt }) {
    await sleep(350);
    const results = { posts: [], failures: [] };
    for (const post of this.#current().filter((p) => postIds.includes(p.postId))) {
      if (post.published) {
        results.failures.push({ postId: post.postId, message: 'This post has already been published, so it can no longer be changed.', reason: 'published' });
        continue;
      }
      const stored = this.posts.find((p) => p.postId === post.postId);
      if (text !== undefined) stored.text = text;
      if (dueAt) stored.dueAt = dueAt.toISOString();
      results.posts.push(stored);
    }
    return normalizePostResults(results);
  }

  async deletePosts(postIds) {
    await sleep(350);
    const published = this.#current().filter((p) => p.published && postIds.includes(p.postId)).map((p) => p.postId);
    const deleted = postIds.filter((id) => !published.includes(id));
    this.posts = this.posts.filter((p) => !deleted.includes(p.postId));
    return normalizePostResults({
      deleted, failures: published.map((postId) => ({ postId, message: 'This post has already been published, so it can no longer be changed.', reason: 'published' }))
    });
  }
}
