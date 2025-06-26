import streamlit as st
import boto3
import io
import base64
import json
from util_funcs import (
    replace_multiple_dashes
)

class PodcastTranscriptApp:
    def __init__(self):
        # Initialize AWS clients
        self.s3 = boto3.client('s3')
        self.bucket_name = 'anique-podcast-summarization'  # Replace with your actual bucket name

    def list_podcast_folders(self):
        """Extract unique podcast names from MP3 files"""
        podcast_names = set()
        
        # List MP3 files in the audio_files directory
        response = self.s3.list_objects_v2(
            Bucket=self.bucket_name, 
            Prefix='audio_files/'
        )
        
        if 'Contents' in response:
            # Extract podcast names by splitting filenames
            podcast_names = {
                obj['Key'].split('/')[-1].split('_')[1] 
                for obj in response['Contents'] 
                if obj['Key'].endswith('.mp3')
            }
        
        return sorted(list(podcast_names))
    
    def list_podcast_episodes(self, podcast_name):
        """List MP3 files for a specific podcast"""
        episodes = []
        
        response = self.s3.list_objects_v2(
            Bucket=self.bucket_name, 
            Prefix='audio_files/'
        )
        
        if 'Contents' in response:
            mp3_files = [obj['Key'].split('/')[-1] for obj in response['Contents'] if obj['Key'].endswith('.mp3')]
            episodes = [
                mp3_file for mp3_file in mp3_files if mp3_file.split('_')[1] == podcast_name
            ]
        return episodes
    
    def get_transcript_files(self, audio_file):
        """List transcript files for a specific podcast"""
        transcripts = []
        job_name = f'transcription-job-{audio_file.replace(' ', '-')}'
        job_name = replace_multiple_dashes(job_name).replace("'", "").replace(",", "").replace('.mp3', '').replace('–', '')
        response = self.s3.list_objects_v2(
            Bucket=self.bucket_name, 
            Prefix=f'transcription_jobs/{job_name}.json'
        )
        if 'Contents' in response:
            transcripts = [obj['Key'].split('/')[-1] for obj in response['Contents'] if obj['Key'].endswith('.json')]
        # print(transcripts)
        return transcripts
    
    def get_audio_file_url(self, audio_file):
        """Generate a presigned URL for the audio file"""
        if not audio_file:
            print("Error: No audio file specified")
            return None
        
        try:
            # Ensure audio file is in the correct format
            if not audio_file.endswith('.mp3'):
                audio_file += '.mp3'
            
            url = self.s3.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': self.bucket_name,
                    'Key': f'audio_files/{audio_file}'
                },
                ExpiresIn=3600  # URL valid for 1 hour
            )
            return url
        except Exception as e:
            print(f"Error generating audio URL: {e}")
            return None
    
    def load_transcript(self, podcast_name, episode):
        """Load transcript with speaker differentiation"""
        try:
            # Construct transcript filename
            transcript_file = episode.replace('.mp3', '.json')
            
            response = self.s3.get_object(
                Bucket=self.bucket_name, 
                Key=f'transcription_jobs/{transcript_file}'
            )
            
            transcript_content = response['Body'].read().decode('utf-8')
            transcript_data = json.loads(transcript_content)
            
            # Extract speaker-differentiated transcript
            formatted_transcript = []
            current_speaker = "Unknown Speaker"
            current_segment = ""
            
            # Assuming the transcript JSON has items with speaker and content
            items = transcript_data.get('results', {}).get('items', [])
            
            for item in items:
                # Check if the item has speaker information
                if 'speaker_label' in item:
                    speaker = f"**Speaker {item['speaker_label'].replace('spk_', '')}**"
                else:
                    speaker = current_speaker
                
                # If speaker changes, add previous segment to transcript
                if speaker != current_speaker and current_segment:
                    formatted_transcript.append({
                        'speaker': current_speaker,
                        'text': current_segment.strip()
                    })
                    current_segment = ""
                
                # Add current item's text
                if 'alternatives' in item and item['alternatives']:
                    current_segment += f" {item['alternatives'][0].get('content', '')}"
                
                current_speaker = speaker
            
            # Add last segment
            if current_segment:
                formatted_transcript.append({
                    'speaker': current_speaker,
                    'text': current_segment.strip()
                })
            
            # Convert to readable format if needed
            formatted_text = "\n".join([f"{entry['speaker']}: {entry['text']}" for entry in formatted_transcript])
            
            return formatted_text or "No transcript available"
    
        except Exception as e:
            print(f"Error loading transcript: {e}")
            return "Transcript could not be loaded."

    def get_audio_file_url(self, audio_file):
        """Generate a presigned URL for the audio file"""
        try:
            url = self.s3.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': self.bucket_name,
                    'Key': f'audio_files/{audio_file}'
                },
                ExpiresIn=7_200  # URL valid for 1 hour
            )
            return url
        except Exception as e:
            st.error(f"Error generating audio URL: {e}")
            return None
    @st.cache_data
    def get_bedrock_summary(_self, transcript):
        bedrock_runtime = boto3.client(
                                        service_name='bedrock-runtime',
                                        region_name='us-west-2'
        )
        system_prompt = """
        You are an expert trader that can select stocks to invest in based on podcast transcripts. Provide a list of stocks, ETFs, or potential areas to invest in based on the transcript provided. Follow this format:
        1. Area/Stock/ETF to invest in
            <justification>Justification 1</justification>
            <justification>Justification 2</justification>
            <justification>Justification 3</justification>
            Examples:
                <examples>Examples of Stocks/ETFs, comma separated</examples>
        
        2. Area/Stock/ETF to invest in
            <justification>Justification 1</justification>
            <justification>Justification 2</justification>
            <justification>Justification 3</justification>
            Examples:
                <examples>Examples of Stocks/ETFs, comma separated</examples>

        Do not include the XML tags in the final response.
        """
        request = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 10_000,
        "messages": [
            {
                "role": "user",
                "content": transcript
            }
        ],
        "temperature": 0.2,
        "top_p": 0.2,
        "system": system_prompt
        }
        
        response = bedrock_runtime.invoke_model(
            modelId='us.anthropic.claude-3-7-sonnet-20250219-v1:0',
            body=json.dumps(request)
        )
        
        response_body = json.loads(response['body'].read())
        
        # Find the 'text' content block in the response
        text_content = None
        for content_block in response_body['content']:
            if content_block['type'] == 'text':
                text_content = content_block['text']
                break
        return text_content
    def run(self):
        """Main Streamlit application"""
        st.set_page_config(layout = 'wide')
        st.title("Podcast Investing Tips")
        
        # # Podcast selection
        # podcasts = self.list_podcast_folders()
        # selected_podcast = st.selectbox("Select a Podcast", podcasts)

        # # Episode selection
        # episodes = self.list_podcast_episodes(selected_podcast)
        # selected_episode = st.selectbox("Select an Episode", episodes)

        # # Transcript selection
        # transcripts = self.get_transcript_files(selected_episode)
        # selected_transcript = st.selectbox("Select a Transcript", transcripts)

        # Create columns for audio and transcript
        col1, col2 = st.columns(2)

        with st.sidebar:
            st.title('Podcast Options')
            st.image('morningstar_logo.png')
            # Podcast selection
            podcasts = self.list_podcast_folders()
            selected_podcast = st.selectbox("Select a Podcast", podcasts)
    
            # Episode selection
            episodes = self.list_podcast_episodes(selected_podcast)
            selected_episode = st.selectbox("Select an Episode", episodes)
    
            # Transcript selection
            transcripts = self.get_transcript_files(selected_episode)
            selected_transcript = st.selectbox("Select a Transcript", transcripts)

            st.subheader("Listen to your Podcast")
            audio_url = self.get_audio_file_url(selected_episode)
            
            if audio_url:
                st.audio(audio_url, format='audio/mp3')
            else:
                st.error("Could not load audio file")
        with col1:
            st.subheader('AI Stock Picks')
            if selected_transcript:
                model_summary = self.get_bedrock_summary(self.load_transcript(selected_podcast, selected_transcript))
                st.markdown(model_summary)
            else:
                st.warning("No AI Summary available")
        with col2:
            # Audio Player

            # Transcript Display
            st.subheader("Transcript")
            if selected_transcript:
                transcript = self.load_transcript(selected_podcast, selected_transcript)
                st.text_area("Transcript Content", transcript, height = 400)
                #st.markdown("Transcript Content", transcript, scrollable = True)
                
            else:
                st.warning("No transcript available")
            
                
def main():
    # Create and run the app
    app = PodcastTranscriptApp()
    app.run()

if __name__ == "__main__":
    main()