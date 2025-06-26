import os
import re
import json
import requests

import boto3
from botocore.exceptions import ClientError
from listennotes import podcast_api

def create_bucket_with_folders(bucket_name):
    # Create S3 client
    s3 = boto3.client('s3')
    
    try:
        # Try to create the bucket
        s3.create_bucket(
            Bucket=bucket_name,
            CreateBucketConfiguration={'LocationConstraint': 'us-west-2'}  # Specify your region
        )
        print(f"Bucket {bucket_name} created successfully.")
    except ClientError as e:
        if e.response['Error']['Code'] == 'BucketAlreadyOwnedByYou':
            print(f"Bucket {bucket_name} already exists.")
        else:
            print(f"Error creating bucket: {e}")
            return
    
    # Create placeholder files in subfolders to ensure they exist
    try:
        # Create audio_files folder
        s3.put_object(Bucket=bucket_name, Key='audio_files/')
        
        # Create transcription_jobs folder
        s3.put_object(Bucket=bucket_name, Key='transcription_jobs/')
        
        print("Subfolders 'audio_files' and 'transcription_jobs' created.")
    except ClientError as e:
        print(f"Error creating subfolders: {e}")

def download_and_upload_podcast_audio(podcast_id):
    # Initialize ListenNotes API client
    # Make sure to set LISTEN_NOTES_API_KEY in your environment
    client = podcast_api.Client(api_key='') # NOTE: API Key goes here
    
    # S3 client
    s3 = boto3.client('s3')
    bucket_name = 'anique-podcast-summarization'
    
    try:
        print(f'Fetching podcast with id: {podcast_id}')
        # Fetch podcast details
        podcast = client.fetch_podcast_by_id(
            id=podcast_id,
            next_episode_pub_date=0,
            sort='recent_first'
        )
        podcast_json = podcast.json()

        podcast_publisher = re.sub(r'[^\w\-_\s]', '', podcast_json.get('publisher')).strip()
        podcast_title = re.sub(r'[^\w\-_\s]', '', podcast_json.get('title')).strip()

        episode_title_audiourl_thumbnail = [(episode.get('title'), episode.get('audio'), episode.get('thumbnail')) for episode in podcast_json.get('episodes')]
        for i, (title, audio_url, thumbnail) in enumerate(episode_title_audiourl_thumbnail, start = 1):
            if not title:
                episode_title = f'Untitled_{i}'
            else:
                episode_title = re.sub(r'[^\w\-_\s]', '', title)
                episode_title = re.sub(r'\s+', '_', episode_title)
            filename = f"{podcast_publisher}_{podcast_title}_{episode_title}.mp3"

            # Download audio file
            print(f'Retrieving audio for {filename}')
            response = requests.get(audio_url)

            if response.status_code == 200:
                # Upload to S3
                s3.put_object(
                    Bucket=bucket_name,
                    Key=f"audio_files/{filename}",
                    Body=response.content
                )
                print(f"Uploaded {filename} to S3")
            else:
                print(f"Failed to download audio from {audio_url}")
    
    except Exception as e:
        print(f"Error processing podcast: {e}")

def replace_multiple_dashes(text):
    return re.sub(r'-+', '-', text)

def list_audio_files(bucket_name, folder_prefix):
    """
    List audio files in the specified S3 bucket and folder
    
    Args:
        bucket_name (str): Name of the S3 bucket
        folder_prefix (str): Folder path/prefix in the bucket
    
    Returns:
        list: A list of tuples [(bucket_name, file_key), ...]
    """
    # Initialize S3 client
    s3_client = boto3.client('s3')
    
    # Common audio file extensions
    audio_extensions = ('.mp3', '.wav', '.m4a', '.aac', '.wma', '.ogg', '.flac')
    
    # List to store audio file tuples
    audio_files = []
    
    try:
        # Ensure folder_prefix ends with '/' if not empty
        if folder_prefix and not folder_prefix.endswith('/'):
            folder_prefix += '/'
            
        # List objects in the bucket with the specified prefix
        paginator = s3_client.get_paginator('list_objects_v2')
        page_iterator = paginator.paginate(
            Bucket=bucket_name,
            Prefix=folder_prefix
        )
        
        print(f"Audio files in {bucket_name}/{folder_prefix}:")
        print("-" * 50)
        
        found_files = False
        for page in page_iterator:
            if 'Contents' in page:
                for obj in page['Contents']:
                    # Check if the file has an audio extension
                    if obj['Key'].lower().endswith(audio_extensions):
                        # Add tuple of (bucket_name, file_key) to the list
                        audio_files.append((bucket_name, obj['Key']))
                        
                        # Print file details
                        print(f"File: {obj['Key']}")
                        print(f"Size: {obj['Size']/1024/1024:.2f} MB")
                        print(f"Last Modified: {obj['LastModified']}")
                        print("-" * 50)
                        found_files = True
        
        if not found_files:
            print("No audio files found in the specified location.")
        
        return audio_files
            
    except ClientError as e:
        print(f"Error accessing S3: {e}")
        return []
    except Exception as e:
        print(f"An error occurred: {e}")
        return []


def transcribe_audio_file(s3_bucket, s3_audio_key, job_name):
    # Create Transcribe client
    transcribe_client = boto3.client('transcribe')
    
    # Start transcription job
    transcribe_client.start_transcription_job(
        TranscriptionJobName=job_name,
        Media={
            'MediaFileUri': f's3://{s3_bucket}/{s3_audio_key}'
        },
        Settings={
            'ShowSpeakerLabels': True,
            'MaxSpeakerLabels': 10
        },
        MediaFormat='mp3',  # Specify your audio format
        LanguageCode='en-US',
        OutputBucketName = s3_bucket,
        OutputKey = 'transcription_jobs/'
    )
    print(f'Transcription with job name {job_name} sent to AWS Transcribe')

def process_audio_files(audio_files):
    transcription_job_names = []
    for s3_bucket, audio_file in audio_files:
        #print(audio_file)
        job_name = f'transcription-job-{audio_file.split('/')[1].replace(' ', '-')}'
        job_name = replace_multiple_dashes(job_name).replace("'", "").replace(",", "").replace('.mp3', '').replace('–', '')
        transcription_job_names.append(job_name)
        #job_name = job_name.replace("'", "")
        try:
            print(job_name)
            transcribe_audio_file(s3_bucket, audio_file, job_name)
        except Exception as e:
            print(f"Error processing {audio_file}: {e}")
    return transcription_job_names

def parse_transcribe_json(file_path):
    """
    Parse Amazon Transcribe JSON file and return a formatted transcript with speakers.
    
    Args:
        file_path (str): Path to the Transcribe JSON file
    
    Returns:
        str: Formatted transcript with speakers
    """
    try:
        # Read the JSON file
        with open(file_path, 'r') as file:
            transcript_data = json.load(file)
        
        # Initialize an empty list to store formatted transcript lines
        formatted_transcript = []
        
        # Check if it's a speaker diarization transcript
        if 'results' in transcript_data and 'speaker_labels' in transcript_data['results']:
            # Extract speaker segments
            speaker_segments = transcript_data['results']['speaker_labels']['segments']
            
            # Extract items (words/punctuation)
            items = transcript_data['results']['items']
            
            # Process each speaker segment
            for segment in speaker_segments:
                speaker = segment['speaker_label']
                start_time = segment['start_time']
                end_time = segment['end_time']
                
                # Collect text for this segment
                segment_text = []
                for item in items:
                    # Check if item is within this segment's time range
                    if (float(item.get('start_time', 0)) >= float(start_time) and 
                        float(item.get('end_time', 0)) <= float(end_time)):
                        
                        # Append the word or punctuation
                        if item['type'] == 'pronunciation':
                            segment_text.append(item['alternatives'][0]['content'])
                        elif item['type'] == 'punctuation':
                            # Append punctuation to the last word
                            if segment_text:
                                segment_text[-1] += item['alternatives'][0]['content']
                
                # Join the segment text and add to formatted transcript
                if segment_text:
                    speaker = speaker.replace('spk_', 'Speaker ')
                    formatted_line = f"{speaker}: {' '.join(segment_text)}"
                    formatted_transcript.append(formatted_line)
        
        # Fallback for non-diarization transcripts
        else:
            # Simple parsing of transcript
            if 'results' in transcript_data and 'transcripts' in transcript_data['results']:
                formatted_transcript.append(transcript_data['results']['transcripts'][0]['transcript'])
        
        # Return the formatted transcript as a string
        return '\n'.join(formatted_transcript)
    
    except Exception as e:
        return f"Error parsing transcript: {str(e)}"