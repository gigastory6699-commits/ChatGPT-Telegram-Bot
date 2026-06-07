from dsk.api import DeepSeekAPI

# Initialize with your auth token
api = DeepSeekAPI("lqf8UhCDk55Spdn5H0OlFzgDV9CMZsjlBpWNh8s8umAY1arTKTYutUyv8kMnFl46")

# Create a new chat session
chat_id = api.create_chat_session()

# Simple chat completion
prompt = "What is Python?"
for chunk in api.chat_completion(chat_id, prompt):
    if chunk['type'] == 'text':
        print(chunk['content'], end='', flush=True)
