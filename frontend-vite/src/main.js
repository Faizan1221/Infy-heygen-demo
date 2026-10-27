
import { LiveAvatarSession } from '@heygen/liveavatar-web-sdk';

/* =========================================================
   Configuration and state
========================================================= */

let avatar = null;
let countdownTimer = null;
let attachTimer = null;

let sessionConnected = false;
let sessionStarting = false;
let sessionGeneration = 0;

let currentVideoUrl = null;
let currentRequestGeneration = 0;
let avatarRequestInProgress = false;

const SESSION_DURATION = 30 * 60;
const API_BASE = 'http://localhost:5000';

const SADTALKER_POLL_INTERVAL = 2000;
const SADTALKER_TIMEOUT = 11 * 60 * 1000;

const $ = (id) => document.getElementById(id);

/* =========================================================
   Status and buttons
========================================================= */

function setStatus(message) {
  const element = $('status-label');
  if (element) element.textContent = message;
}

function setStartButton(text, disabled = false) {
  const button = $('start-btn');
  if (!button) return;

  button.innerText = text;
  button.disabled = disabled;
}

function setAskButtonDisabled(disabled) {
  const button = $('ask-btn');
  if (button) button.disabled = disabled;
}

/* =========================================================
   Media helpers
========================================================= */

function getVideoElement() {
  return $('avatar-stream');
}

function showStaticPortrait() {
  const video = getVideoElement();
  const image = $('selected-avatar-image');

  if (video) {
    video.pause();
    video.srcObject = null;
    video.removeAttribute('src');
    video.load();
    video.hidden = true;
  }

  if (image) image.hidden = false;
}

function resetMediaElement() {
  const video = getVideoElement();
  if (!video) return;

  video.pause();
  video.srcObject = null;
  video.removeAttribute('src');
  video.load();

  video.hidden = false;
  video.autoplay = true;
  video.playsInline = true;
}

function showSadTalkerVideo(videoUrl) {
  const video = getVideoElement();
  const image = $('selected-avatar-image');

  if (!video) {
    throw new Error('Missing video element #avatar-stream in index.html.');
  }

  // Never show the static portrait and avatar video together.
  if (image) image.hidden = true;

  video.pause();
  video.srcObject = null;
  video.removeAttribute('src');

  video.autoplay = false;
  video.controls = true;
  video.playsInline = true;
  video.muted = false;
  video.hidden = false;

  // Cache-busting prevents a previous answer from being reused.
  video.src = `${API_BASE}${videoUrl}?t=${Date.now()}`;
  video.load();
}

function logMediaDiagnostics(stage) {
  const video = getVideoElement();

  if (!video) {
    console.error('Video element #avatar-stream was not found.');
    return;
  }

  const stream = video.srcObject;

  console.log(`[Avatar media: ${stage}]`, {
    connected: sessionConnected,
    srcObjectPresent: Boolean(stream),
    videoSource: video.currentSrc || video.src || null,
    paused: video.paused,
    muted: video.muted,
    readyState: video.readyState,
    videoWidth: video.videoWidth,
    videoHeight: video.videoHeight,
    audioTracks: stream?.getAudioTracks?.().map((track) => ({
      enabled: track.enabled,
      muted: track.muted,
      readyState: track.readyState
    })) ?? [],
    videoTracks: stream?.getVideoTracks?.().map((track) => ({
      enabled: track.enabled,
      muted: track.muted,
      readyState: track.readyState
    })) ?? []
  });
}

/* =========================================================
   LiveAvatar session timers
========================================================= */

function clearTimers() {
  if (countdownTimer) clearInterval(countdownTimer);
  if (attachTimer) clearInterval(attachTimer);

  countdownTimer = null;
  attachTimer = null;
}

function stopCountdown() {
  if (countdownTimer) clearInterval(countdownTimer);
  countdownTimer = null;

  const display = $('countdown-display');
  if (display) display.innerText = '';
}

function startCountdown(generation) {
  stopCountdown();

  const endTime = Date.now() + SESSION_DURATION * 1000;
  const display = $('countdown-display');

  const updateCountdown = () => {
    if (generation !== sessionGeneration) return;

    const secondsLeft = Math.max(
      0,
      Math.ceil((endTime - Date.now()) / 1000)
    );

    if (display) {
      const mins = Math.floor(secondsLeft / 60);
      const secs = secondsLeft % 60;

      display.innerText =
        `Fallback session timer: ${mins}:${secs.toString().padStart(2, '0')}`;

      display.style.color =
        secondsLeft <= 60 ? '#f0883e' : '#8b949e';
    }

    if (secondsLeft <= 0) {
      stopCountdown();

      setStatus(
        'LiveAvatar fallback session timer ended. Restart if required.'
      );

      setStartButton('Restart Fallback Avatar', false);
    }
  };

  updateCountdown();
  countdownTimer = setInterval(updateCountdown, 1000);
}

/* =========================================================
   Stop LiveAvatar when switching to SadTalker
========================================================= */

async function stopLiveAvatarSession() {
  const previousAvatar = avatar;

  // Invalidate callbacks belonging to the previous session.
  sessionGeneration++;

  sessionConnected = false;
  sessionStarting = false;

  clearTimers();
  stopCountdown();

  avatar = null;

  const video = getVideoElement();

  if (video) {
    video.pause();
    video.srcObject = null;
    video.removeAttribute('src');
    video.load();
    video.hidden = true;
  }

  if (previousAvatar && typeof previousAvatar.stop === 'function') {
    try {
      await previousAvatar.stop();
    } catch (error) {
      console.warn('Could not cleanly stop the previous LiveAvatar session.', error);
    }
  }
}

function handleSessionEnded(generation, reason = 'Fallback session ended') {
  if (generation !== sessionGeneration) return;

  sessionConnected = false;
  sessionStarting = false;

  clearTimers();
  stopCountdown();

  console.warn('LiveAvatar session ended:', reason);

  const video = getVideoElement();

  if (video) {
    video.pause();
    video.srcObject = null;
    video.hidden = true;
  }

  setStatus(`${reason}. SadTalker remains the primary avatar engine.`);
  setStartButton('Retry Fallback Avatar', false);
}

/* =========================================================
   HeyGen LiveAvatar fallback
========================================================= */

/*
 * This function is called automatically only if SadTalker
 * generation or playback fails. The Start button can also
 * be used to manually test the fallback.
 */

async function initAvatar() {
  if (sessionStarting) return;

  if (sessionConnected && avatar) {
    setStatus('LiveAvatar fallback session is already active.');
    return;
  }

  sessionStarting = true;
  sessionConnected = false;

  clearTimers();
  stopCountdown();

  const generation = ++sessionGeneration;

  setStartButton('Starting Fallback...', true);
  setStatus('SadTalker was unavailable. Starting LiveAvatar fallback...');

  const video = getVideoElement();

  if (!video) {
    sessionStarting = false;
    setStartButton('Retry Fallback Avatar', false);
    setStatus('Missing video element #avatar-stream in index.html.');
    return;
  }

  resetMediaElement();

  const image = $('selected-avatar-image');
  if (image) image.hidden = true;

  try {
    const response = await fetch(`${API_BASE}/liveavatar/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' }
    });

    if (!response.ok) {
      const errorText = await response.text();

      throw new Error(
        `LiveAvatar API returned HTTP ${response.status}: ${errorText}`
      );
    }

    const result = await response.json();
    const sessionToken = result?.data?.session_token;

    if (!sessionToken) {
      throw new Error(
        'The LiveAvatar response did not contain data.session_token.'
      );
    }

    if (generation !== sessionGeneration) return;

    const userConfig = {
      voiceChat: { defaultMuted: true },
      autoKeepAlive: true
    };

    const newAvatar = new LiveAvatarSession(sessionToken, userConfig);
    avatar = newAvatar;

    try {
      newAvatar.on('disconnected', () => {
        handleSessionEnded(generation, 'LiveAvatar disconnected');
      });
    } catch (eventError) {
      console.warn('Could not register LiveAvatar disconnect handler.', eventError);
    }

    setStatus('Connecting to LiveAvatar fallback...');

    await newAvatar.start();

    if (generation !== sessionGeneration) {
      try {
        await newAvatar.stop();
      } catch {
        // The session has already been superseded.
      }
      return;
    }

    sessionConnected = true;
    sessionStarting = false;

    setStatus('LiveAvatar fallback connected. Waiting for media...');

    let attempts = 0;
    const maxAttempts = 30;

    attachTimer = setInterval(async () => {
      if (generation !== sessionGeneration) {
        clearInterval(attachTimer);
        attachTimer = null;
        return;
      }

      attempts++;

      try {
        if (!video.srcObject) {
          newAvatar.attach(video);
        }

        const stream = video.srcObject;
        const hasVideo = Boolean(stream?.getVideoTracks?.().length);
        const hasAudio = Boolean(stream?.getAudioTracks?.().length);

        if (hasVideo || hasAudio) {
          try {
            await video.play();
          } catch (playError) {
            console.warn('Browser requires a user gesture for playback.', playError);
          }
        }

        if (hasVideo && hasAudio) {
          clearInterval(attachTimer);
          attachTimer = null;

          logMediaDiagnostics('LiveAvatar fallback attached');

          setStatus(
            'LiveAvatar fallback is connected. You can ask another question.'
          );

          setStartButton('Fallback Connected', true);
          startCountdown(generation);
          return;
        }

        if (attempts >= maxAttempts) {
          clearInterval(attachTimer);
          attachTimer = null;

          logMediaDiagnostics('LiveAvatar fallback attachment timeout');

          setStatus(
            'Fallback connected, but media tracks are incomplete. Check Console.'
          );

          setStartButton('Retry Fallback Avatar', false);
        }
      } catch (error) {
        console.error('Fallback media attachment failed:', error);

        if (attempts >= maxAttempts) {
          clearInterval(attachTimer);
          attachTimer = null;

          setStatus('Fallback media attachment failed.');
          setStartButton('Retry Fallback Avatar', false);
        }
      }
    }, 500);

    startCountdown(generation);

  } catch (error) {
    console.error('LiveAvatar fallback initialization failed:', error);

    if (generation === sessionGeneration) {
      sessionConnected = false;
      sessionStarting = false;
      clearTimers();

      avatar = null;

      showStaticPortrait();

      setStatus(
        `SadTalker failed and LiveAvatar fallback could not start: ${error.message}`
      );

      setStartButton('Retry Fallback Avatar', false);
    }
  }
}

/* =========================================================
   SadTalker job API
========================================================= */

async function createSadTalkerJob(answer, portrait) {
  const payload = { answer };

  // Only send a custom portrait when the user uploaded one.
  if (portrait) {
    payload.portrait = portrait;
  }

  const response = await fetch(`${API_BASE}/sadtalker/jobs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });

  const result = await response.json().catch(() => ({}));

  if (!response.ok) {
    throw new Error(
      result.error || `SadTalker job request failed with HTTP ${response.status}.`
    );
  }

  if (!result.job_id) {
    throw new Error('SadTalker did not return a job ID.');
  }

  return result.job_id;
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function pollSadTalkerJob(jobId, requestGeneration) {
  const startedAt = Date.now();

  while (Date.now() - startedAt < SADTALKER_TIMEOUT) {
    if (requestGeneration !== currentRequestGeneration) {
      throw new Error('This avatar request was superseded by a newer request.');
    }

    const response = await fetch(
      `${API_BASE}/sadtalker/jobs/${encodeURIComponent(jobId)}`,
      { cache: 'no-store' }
    );

    const result = await response.json().catch(() => ({}));

    if (!response.ok) {
      throw new Error(
        result.error || `SadTalker status request failed: HTTP ${response.status}.`
      );
    }

    if (result.status === 'completed') {
      if (!result.video_url) {
        throw new Error('SadTalker completed without returning a video URL.');
      }

      return result.video_url;
    }

    if (result.status === 'failed') {
      throw new Error(result.error || 'SadTalker video generation failed.');
    }

    const stageMessages = {
      queued: 'SadTalker request queued...',
      processing: 'SadTalker is preparing your avatar...',
      preparing: 'Preparing portrait and generation environment...',
      generating_audio: 'Generating speech audio...',
      generating_video: 'SadTalker is animating the portrait...',
      completed: 'Avatar video generated.'
    };

    setStatus(
      stageMessages[result.stage] ||
      'SadTalker is generating the avatar video...'
    );

    await delay(SADTALKER_POLL_INTERVAL);
  }

  throw new Error('SadTalker generation timed out in the frontend.');
}

/* =========================================================
   Play SadTalker output
========================================================= */

function waitForVideoEvent(video, eventName, timeoutMs = 15000) {
  return new Promise((resolve, reject) => {
    let timeout;

    const cleanup = () => {
      clearTimeout(timeout);
      video.removeEventListener(eventName, onSuccess);
      video.removeEventListener('error', onError);
    };

    const onSuccess = () => {
      cleanup();
      resolve();
    };

    const onError = () => {
      cleanup();
      reject(new Error('The generated SadTalker video could not be loaded.'));
    };

    timeout = setTimeout(() => {
      cleanup();
      reject(new Error('Timed out while loading the generated video.'));
    }, timeoutMs);

    video.addEventListener(eventName, onSuccess, { once: true });
    video.addEventListener('error', onError, { once: true });

    // Handle an event that happened before the listeners were added.
    if (eventName === 'loadedmetadata' && video.readyState >= 1) {
      cleanup();
      resolve();
    }
  });
}

async function playSadTalkerVideo(videoUrl, requestGeneration) {
  // Do not let a streaming fallback and a generated clip play together.
  await stopLiveAvatarSession();

  if (requestGeneration !== currentRequestGeneration) {
    throw new Error('A newer avatar request has started.');
  }

  const video = getVideoElement();

  if (!video) {
    throw new Error('Missing video element #avatar-stream in index.html.');
  }

  showSadTalkerVideo(videoUrl);
  currentVideoUrl = videoUrl;

  try {
    await waitForVideoEvent(video, 'loadedmetadata');

    if (requestGeneration !== currentRequestGeneration) {
      throw new Error('A newer avatar request has started.');
    }

    await video.play();

    setStatus('SadTalker avatar is speaking.');

    setStartButton('SadTalker Active', true);

    logMediaDiagnostics('SadTalker video playing');

    video.onended = () => {
      if (requestGeneration !== currentRequestGeneration) return;

      setStatus(
        'SadTalker has finished speaking. Ask another question to continue.'
      );

      setStartButton('Play Answer Again', false);
    };

    video.onerror = () => {
      if (requestGeneration !== currentRequestGeneration) return;

      console.error('SadTalker video playback error.');

      setStatus('SadTalker video playback failed.');
    };

  } catch (error) {
    video.pause();
    video.onended = null;
    video.onerror = null;

    throw error;
  }
}

/* =========================================================
   Main question flow
========================================================= */

async function askQuestion() {
  const input = $('question-input');
  const question = input?.value?.trim();

  if (!question) {
    alert('Please enter a question.');
    return;
  }

  if (avatarRequestInProgress) {
    setStatus('Please wait for the current avatar request to finish.');
    return;
  }

  avatarRequestInProgress = true;
  setAskButtonDisabled(true);

  const requestGeneration = ++currentRequestGeneration;

  // Stop a previous clip before requesting another answer.
  const video = getVideoElement();

  if (video && !video.srcObject) {
    video.pause();
  }

  setStatus('Thinking...');

  try {
    const response = await fetch(`${API_BASE}/ask`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question })
    });

    if (!response.ok) {
      throw new Error(`Question API returned HTTP ${response.status}.`);
    }

    const result = await response.json();

    const answer = result?.answer;
    const source = result?.source;

    if (typeof answer !== 'string' || !answer.trim()) {
      throw new Error('The backend did not return a usable answer.');
    }

    const answerElement = $('answer-text');
    if (answerElement) answerElement.innerText = answer;

    const citation = $('source-citation');

    if (citation) {
      citation.style.display = 'block';
      citation.innerText = source
        ? `Source: ${source}`
        : 'Source: Not provided';
    }

    if (requestGeneration !== currentRequestGeneration) return;

    /*
     * PRIMARY ENGINE: SADTALKER
     *
     * The uploaded portrait is optional. Without one, the backend
     * uses SadTalker's configured default portrait.
     */

    let sadTalkerError;

    try {
      setStatus('Starting SadTalker avatar generation...');

      // Ensure a previously running streaming session does not remain visible.
      await stopLiveAvatarSession();

      if (requestGeneration !== currentRequestGeneration) return;

      const jobId = await createSadTalkerJob(
        answer.trim(),
        selectedPortraitData
      );

      setStatus('SadTalker is generating your avatar video...');

      const videoUrl = await pollSadTalkerJob(
        jobId,
        requestGeneration
      );

      if (requestGeneration !== currentRequestGeneration) return;

      await playSadTalkerVideo(videoUrl, requestGeneration);

      // SadTalker succeeded: do not start LiveAvatar.
      return;

    } catch (error) {
      sadTalkerError = error;

      if (requestGeneration !== currentRequestGeneration) return;

      console.error('SadTalker primary engine failed:', error);

      setStatus(
        `SadTalker failed. Starting LiveAvatar fallback...`
      );
    }

    /*
     * FALLBACK ENGINE: LIVEAVATAR
     *
     * Reached only when SadTalker generation or playback fails.
     */

    if (requestGeneration !== currentRequestGeneration) return;

    try {
      await initAvatar();

      if (requestGeneration !== currentRequestGeneration) return;

      if (!sessionConnected || !avatar) {
        throw new Error(
          'The LiveAvatar fallback session did not connect successfully.'
        );
      }

      setStatus('Sending the answer to LiveAvatar fallback...');

      const activeAvatar = avatar;

      await activeAvatar.repeat(answer.trim());

      if (requestGeneration !== currentRequestGeneration) return;

      setStatus(
        'LiveAvatar fallback is speaking. SadTalker could not complete this request.'
      );

      logMediaDiagnostics('fallback speech submitted');

    } catch (fallbackError) {
      console.error('LiveAvatar fallback failed:', fallbackError);

      if (requestGeneration !== currentRequestGeneration) return;

      showStaticPortrait();

      setStatus(
        `Both avatar engines failed. SadTalker: ${sadTalkerError?.message || 'unknown error'}. ` +
        `LiveAvatar: ${fallbackError.message}`
      );

      setStartButton('Retry Fallback Avatar', false);
    }

  } catch (error) {
    console.error('Question request failed:', error);

    if (requestGeneration === currentRequestGeneration) {
      setStatus(`Could not get an answer: ${error.message}`);
    }

  } finally {
    if (requestGeneration === currentRequestGeneration) {
      avatarRequestInProgress = false;
      setAskButtonDisabled(false);
    }
  }
}

/* =========================================================
   Example questions
========================================================= */

function fillQuestion(value) {
  if (!value) return;

  const input = $('question-input');
  if (input) input.value = value;
}

/* =========================================================
   Avatar settings
========================================================= */

const avatarSettingsToggle = $('avatar-settings-toggle');
const avatarSettingsPanel = $('avatar-settings-panel');
const avatarCards = document.querySelectorAll('.avatar-card');
const selectedAvatarImage = $('selected-avatar-image');
const avatarPortrait = $('avatar-portrait');
const avatarPreview = $('avatar-preview');
const avatarSettingsStatus = $('avatar-settings-status');
const saveAvatarSettingsButton = $('save-avatar-settings');

const validChoices = [
  'default',
  'professional',
  'friendly',
  'tech',
  'support',
  'executive'
];

const avatarImages = {
  default: '/avatars/default-liveavatar.jpg',
  professional: '/avatars/professional-assistant.jpg',
  friendly: '/avatars/friendly-assistant.jpg',
  tech: '/avatars/tech-assistant.jpg',
  support: '/avatars/support-specialist.jpg',
  executive: '/avatars/executive-assistant.jpg'
};

let selectedAvatarChoice = 'default';
let selectedPortraitData = null;

function setAvatarSettingsStatus(message) {
  if (avatarSettingsStatus) {
    avatarSettingsStatus.textContent = message;
  }
}

function selectAvatar(choice) {
  if (!validChoices.includes(choice)) return;

  selectedAvatarChoice = choice;

  avatarCards.forEach((card) => {
    const selected = card.dataset.avatar === choice;

    card.classList.toggle('selected', selected);
    card.setAttribute('aria-pressed', String(selected));
  });

  if (selectedAvatarImage) {
    selectedAvatarImage.src = avatarImages[choice];
  }
}

avatarCards.forEach((card) => {
  card.addEventListener('click', () => {
    const choice = card.dataset.avatar;

    if (!validChoices.includes(choice)) {
      setAvatarSettingsStatus('This avatar option is not configured.');
      return;
    }

    selectAvatar(choice);

    setAvatarSettingsStatus(
      'Avatar selected. Save to keep your preference.'
    );
  });
});

function loadAvatarSettings() {
  try {
    const saved = localStorage.getItem('abc-avatar-settings');

    if (!saved) {
      selectAvatar('default');
      return;
    }

    const settings = JSON.parse(saved);

    if (settings && validChoices.includes(settings.choice)) {
      selectAvatar(settings.choice);
    } else {
      selectAvatar('default');
    }

    if (
      typeof settings?.portrait === 'string' &&
      settings.portrait.startsWith('data:image/') &&
      avatarPreview
    ) {
      selectedPortraitData = settings.portrait;

      avatarPreview.src = selectedPortraitData;
      avatarPreview.hidden = false;
      avatarPreview.style.display = 'block';
    }

    setAvatarSettingsStatus('Your saved avatar preference has been loaded.');

  } catch (error) {
    console.error('Could not load avatar settings:', error);

    setAvatarSettingsStatus(
      'Saved settings could not be loaded. Select an avatar and save again.'
    );
  }
}

if (avatarSettingsToggle && avatarSettingsPanel) {
  avatarSettingsToggle.addEventListener('click', () => {
    const willOpen = avatarSettingsPanel.hidden;

    avatarSettingsPanel.hidden = !willOpen;

    avatarSettingsToggle.setAttribute(
      'aria-expanded',
      String(willOpen)
    );
  });
}

if (avatarPortrait) {
  avatarPortrait.addEventListener('change', () => {
    const file = avatarPortrait.files?.[0];

    if (!file) return;

    const allowedTypes = [
      'image/png',
      'image/jpeg',
      'image/webp'
    ];

    const maxSizeBytes = 2 * 1024 * 1024;

    if (!allowedTypes.includes(file.type)) {
      avatarPortrait.value = '';

      setAvatarSettingsStatus(
        'Please choose a PNG, JPG, or WEBP image.'
      );

      return;
    }

    if (file.size > maxSizeBytes) {
      avatarPortrait.value = '';

      setAvatarSettingsStatus(
        'Please choose an image smaller than 2 MB.'
      );

      return;
    }

    const reader = new FileReader();

    reader.onload = () => {
      if (typeof reader.result !== 'string') {
        setAvatarSettingsStatus('Could not preview this image.');
        return;
      }

      selectedPortraitData = reader.result;

      if (avatarPreview) {
        avatarPreview.src = selectedPortraitData;
        avatarPreview.hidden = false;
        avatarPreview.style.display = 'block';
      }

      setAvatarSettingsStatus(
        'Portrait preview ready. Save to use this portrait for SadTalker.'
      );
    };

    reader.onerror = () => {
      setAvatarSettingsStatus('Could not read the selected image.');
    };

    reader.readAsDataURL(file);
  });
}

if (saveAvatarSettingsButton) {
  saveAvatarSettingsButton.addEventListener('click', () => {
    try {
      localStorage.setItem(
        'abc-avatar-settings',
        JSON.stringify({
          choice: selectedAvatarChoice,
          portrait: selectedPortraitData
        })
      );

      setAvatarSettingsStatus(
        'Avatar preference saved in this browser.'
      );

    } catch (error) {
      console.error('Could not save avatar settings:', error);

      setAvatarSettingsStatus(
        'Could not save settings. Try removing the uploaded portrait and saving again.'
      );
    }
  });
}

loadAvatarSettings();

/* =========================================================
   Page event handlers
========================================================= */

const startButton = $('start-btn');

if (startButton) {
  startButton.addEventListener('click', initAvatar);
}

window.initAvatar = initAvatar;
window.askQuestion = askQuestion;
window.fillQuestion = fillQuestion;
