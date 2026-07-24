# File: heygen-invoice-demo/backend/upload_kb.py
# Run this ONCE to load both knowledge bases into Pinecone
import json
import os
from openai import OpenAI
from pinecone import Pinecone
from dotenv import load_dotenv

load_dotenv()

openai_client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
pc = Pinecone(api_key=os.getenv('PINECONE_API_KEY'))
index = pc.Index(os.getenv('PINECONE_INDEX_NAME'))

# Both knowledge base files
kb_files = [
    {
        'path': '../knowledge_base/onboarding_kb.json',
        'source': 'ABC Company Onboarding Guide v1.0'
    },
    {
        'path': '../knowledge_base/invoicing_kb.json',
        'source': 'ABC Company Invoice Processing Guide v1.0'
    }
]

for kb_file in kb_files:
    with open(kb_file['path'], 'r') as f:
        kb = json.load(f)

    print(f'\nUploading {len(kb["qa_pairs"])} Q&A pairs from {kb_file["path"]}...')

    for item in kb['qa_pairs']:
        response = openai_client.embeddings.create(
            input=item['question'],
            model='text-embedding-3-large'
        )
        embedding = response.data[0].embedding

        index.upsert(vectors=[{
            'id': item['id'],
            'values': embedding,
            'metadata': {
                'question': item['question'],
                'answer': item['answer'],
                'source': kb_file['source']
            }
        }])
        print(f'  Uploaded {item["id"]}: {item["question"][:50]}...')

print('\nAll knowledge bases uploaded successfully!')