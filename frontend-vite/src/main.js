import { LiveAvatarSession } from '@heygen/liveavatar-web-sdk';

let avatar = null;
let countdownTimer = null;
const SESSION_DURATION = 60; // seconds

function startCountdown() {
  const endTime = Date.now() + SESSION_DURATION * 1000;

  countdownTimer = setInterval(() => {
    const secondsLeft = Math.round((endTime - Date.now()) / 1000);

    if (secondsLeft <= 0) {
      clearInterval(countdownTimer);
      setStatus('Session expired — click "Start Avatar" to continue');
      document.getElementById('countdown-display').innerText = '';
      document.getElementById('start-btn').disabled = false;
      document.getElementById('start-btn').innerText = 'Restart Avatar';
      return;
    }

    const mins = Math.floor(secondsLeft / 60);
    const secs = secondsLeft % 60;
    document.getElementById('countdown-display').innerText = `Session ends in: ${mins}:${secs.toString().padStart(2, '0')}`;
    document.getElementById('countdown-display').style.color = secondsLeft <= 15 ? '#f0883e' : '#8b949e';
  }, 500);
}

async function initAvatar() {
  if (countdownTimer) clearInterval(countdownTimer);

  try {
    document.getElementById('start-btn').disabled = true;
    document.getElementById('start-btn').innerText = 'Starting...';
    document.getElementById('countdown-display').innerText = '';
    setStatus('Starting avatar session...');

    const res = await fetch('https://infy-heygen-demo.onrender.com/liveavatar/start', { method: 'POST' });
    const result = await res.json();
    const sessionToken = result.data.session_token;

    const userConfig = { voiceChat: false };
    avatar = new LiveAvatarSession(sessionToken, userConfig);

    await avatar.start();

    const videoEl = document.getElementById('avatar-stream');
let attempts = 0;
const tryAttach = setInterval(() => {
  attempts++;
  if (!videoEl.srcObject) {
    avatar.attach(videoEl);
  }
  if (videoEl.srcObject || attempts > 20) {
    clearInterval(tryAttach);
    setStatus('Avatar ready');
    document.getElementById('start-btn').innerText = 'Start Avatar';
    startCountdown();
  }
}, 300);

    avatar.on('disconnected', () => {
      clearInterval(countdownTimer);
      setStatus('Session ended — click "Start Avatar" to continue');
      document.getElementById('countdown-display').innerText = '';
      document.getElementById('start-btn').disabled = false;
      document.getElementById('start-btn').innerText = 'Restart Avatar';
    });

  } catch (err) {
    console.error('Avatar init failed:', err);
    setStatus('Avatar failed to start — please try again');
    document.getElementById('start-btn').disabled = false;
    document.getElementById('start-btn').innerText = 'Start Avatar';
    document.getElementById('countdown-display').innerText = '';
  }
}

async function askQuestion() {
  const question = document.getElementById('question-input').value.trim();
  if (!question) { alert('Please enter a question.'); return; }

  document.getElementById('ask-btn').disabled = true;
  setStatus('Thinking...');

  const res = await fetch('https://infy-heygen-demo.onrender.com/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question })
  });
  const { answer, source } = await res.json();

  document.getElementById('answer-text').innerText = answer;
  document.getElementById('source-citation').style.display = 'block';
  document.getElementById('source-citation').innerText = 'Source: ' + source;

  if (avatar) {
    try {
      avatar.repeat(answer);
    } catch (err) {
      console.error('Avatar repeat failed:', err);
    }
  }

  document.getElementById('ask-btn').disabled = false;
  setStatus('Avatar ready');
}

function fillQuestion(val) {
  if (val) document.getElementById('question-input').value = val;
}

function setStatus(msg) {
  document.getElementById('status-label').innerText = msg;
}

document.getElementById('start-btn').onclick = initAvatar;

window.initAvatar = initAvatar;
window.askQuestion = askQuestion;
window.fillQuestion = fillQuestion;
