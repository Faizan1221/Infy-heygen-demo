# File: heygen-invoice-demo/backend/app.py
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from openai import OpenAI
from pinecone import Pinecone
import os, requests
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, static_folder='../frontend-vite/dist', static_url_path='')
CORS(app, origins=[
    "https://infy-heygen-demo1.onrender.com",
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:5000",
]) # Allows the frontend to talk to this server

openai_client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
pc = Pinecone(api_key=os.getenv('PINECONE_API_KEY'))
pinecone_index = pc.Index(os.getenv('PINECONE_INDEX_NAME'))

SYSTEM_PROMPT = '''You are an internal assistant for ABC Company.
You answer questions ONLY based on the context provided below.
You have knowledge about two areas:
1. Employee Onboarding — first day, working hours, leave, dress code, etc.
2. Invoice Processing — ERP logging, PO matching, approvals, payments, etc.
If the answer is not in the provided context, say exactly:
"I could not find that in the company guide. Please speak to your HR representative or Finance Manager."
Never use general knowledge. Always be clear and concise.
Do NOT add a source or citation at the end of your answer.'''

def get_relevant_context(question):
    '''Searches Pinecone for the most relevant content.'''
    response = openai_client.embeddings.create(
        input=question,
        model='text-embedding-3-large'
    )
    q_embedding = response.data[0].embedding

    results = pinecone_index.query(
        vector=q_embedding,
        top_k=3,
        include_metadata=True
    )

    context_parts = []
    source = 'Infylearn Guide v1.0'
    for match in results.matches:
        if match.score > 0.2:
            context_parts.append(match.metadata['answer'])
            source = match.metadata.get('source', source)
    return '\n\n'.join(context_parts), source

def generate_answer(question, context):
    '''Asks GPT-4o to write a natural answer using the onboarding context.'''
    response = openai_client.chat.completions.create(
        model='gpt-4o',
        messages=[
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': f'ONBOARDING CONTEXT:\n{context}\n\nQUESTION: {question}'}
        ],
        max_tokens=400,
        temperature=0
    )
    return response.choices[0].message.content

# --- Serve React Frontend ---
@app.route('/')
def index():
    return send_from_directory('../frontend-vite/dist', 'index.html')

@app.route('/<path:path>')
def serve_static(path):
    if os.path.exists(os.path.join('../frontend-vite/dist', path)):
        return send_from_directory('../frontend-vite/dist', path)
    return send_from_directory('../frontend-vite/dist', 'index.html')

# --- API Routes ---
@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json()
    question = data.get('question', '').strip()

    if not question:
        return jsonify({'error': 'No question provided'}), 400

    context, source = get_relevant_context(question)

    if not context:
        answer = 'I could not find that in the company guide. Please speak to your HR representative or Finance Manager.'
        source = 'Infylearn Guide v1.0'
    else:
        answer = generate_answer(question, context)

    return jsonify({'answer': answer, 'source': source})

@app.route('/heygen/session', methods=['POST'])
def create_heygen_session():
    '''Creates a new HeyGen streaming avatar session.'''
    resp = requests.post(
        'https://api.heygen.com/v1/streaming.new',
        json={
            'quality': 'high',
            'avatar_name': os.getenv('HEYGEN_AVATAR_ID'),
            'voice': {'voice_id': os.getenv('HEYGEN_VOICE_ID')}
        },
        headers={'x-api-key': os.getenv('HEYGEN_API_KEY'), 'Content-Type': 'application/json'}
    )
    return jsonify(resp.json())

@app.route('/heygen/token', methods=['POST'])
def get_heygen_token():
    '''Generates a short-lived access token for the frontend SDK to use.'''
    resp = requests.post(
        'https://api.heygen.com/v1/streaming.create_token',
        headers={'x-api-key': os.getenv('HEYGEN_API_KEY')}
    )
    return jsonify(resp.json())

@app.route('/heygen/speak', methods=['POST'])
def heygen_speak():
    '''Sends text to HeyGen for the avatar to speak.'''
    data = request.get_json()
    resp = requests.post(
        'https://api.heygen.com/v1/streaming.task',
        json={'session_id': data['session_id'], 'text': data['text'], 'task_type': 'talk'},
        headers={'x-api-key': os.getenv('HEYGEN_API_KEY'), 'Content-Type': 'application/json'}
    )
    return jsonify(resp.json())

@app.route('/liveavatar/start', methods=['POST'])
def start_liveavatar_session():
    '''Creates a LiveAvatar FULL mode session token. The SDK handles starting the session itself.'''
    token_resp = requests.post(
        'https://api.liveavatar.com/v1/sessions/token',
        json={
            'mode': 'FULL',
            'avatar_id': os.getenv('LIVEAVATAR_AVATAR_ID'),
            'avatar_persona': {
                'voice_id': os.getenv('LIVEAVATAR_VOICE_ID'),
                'context_id': os.getenv('LIVEAVATAR_CONTEXT_ID'),
                'language': 'en'
            },
            'session_duration': 240,  # 4 minutes (in seconds)
            'is_sandbox': True
        },
        headers={'X-API-KEY': os.getenv('LIVEAVATAR_API_KEY'), 'Content-Type': 'application/json'}
    )
    return jsonify(token_resp.json())

if __name__ == '__main__':
    print('Backend server running on http://localhost:5000')
    app.run(debug=True, port=5000)