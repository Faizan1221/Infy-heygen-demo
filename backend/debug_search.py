import os
from openai import OpenAI
from pinecone import Pinecone
from dotenv import load_dotenv

load_dotenv()

openai_client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
pc = Pinecone(api_key=os.getenv('PINECONE_API_KEY'))
index = pc.Index(os.getenv('PINECONE_INDEX_NAME'))

question = "What are the standard working hours?"

response = openai_client.embeddings.create(
    input=question,
    model='text-embedding-3-large'
)
q_embedding = response.data[0].embedding

results = index.query(
    vector=q_embedding,
    top_k=3,
    include_metadata=True
)

print(f"Question: {question}\n")
for match in results.matches:
    print(f"Score: {match.score}")
    print(f"ID: {match.id}")
    print(f"Question in KB: {match.metadata.get('question')}")
    print(f"Answer: {match.metadata.get('answer')[:80]}...")
    print("---")