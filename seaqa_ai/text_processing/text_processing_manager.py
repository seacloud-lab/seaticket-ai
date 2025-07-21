import logging

logger = logging.getLogger(__name__)


class TextProcessingManager:
    def __init__(self, app, llm_type):
        self.app = app
        self.llm_type = llm_type

    def generate_answer(self, question, sources, context):
        """
        Generate an answer based on the question and references.
        """
        try:
            # Extract top 5 sources
            top_sources = sources[:5] if len(sources) > 5 else sources
            
            # Build reference texts
            reference_texts = []
            for i, source in enumerate(top_sources, 1):
                content = source.get('content', '')
                title = source.get('title', 'Unknown Title')
                url = source.get('url', '')
                
                reference_text = f"Reference {i}:\nTitle: {title}\nContent: {content}"
                if url:
                    reference_text += f"\nSource: {url}"
                reference_texts.append(reference_text)
            
            references_content = "\n\n".join(reference_texts)
            
            # Build prompt
            prompt = f"""Please answer the user's question based on the following reference materials. Ensure the answer is accurate, detailed, and helpful.
    
            User Question: {question}
    
            Reference Materials:
            {references_content}
    
            Please provide a detailed answer based on the above reference materials. If there is insufficient information in the references to fully answer the question, please indicate this."""
            
            # Build message format
            messages = [
                {
                    "role": "system",
                    "content": "You are a professional technical assistant who excels at answering user questions based on provided reference materials. Please provide accurate and detailed answers based on the reference materials."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ]
            
            response = self.app.openai_api.chat_completions(
                messages=messages,
                context=context,
                temperature=0.1,
            )
            
            logger.info(f"Generated answer for question: {question[:50]}...")
            return response
            
        except Exception as e:
            logger.error(f"Error generating answer: {str(e)}")
            return f"Sorry, an error occurred while generating the answer: {str(e)}"
