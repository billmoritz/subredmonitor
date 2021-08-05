#!/bin/env python3

import os
import praw
import pyprowl
import re
import redis
import time
import yaml

# TODO: Better Logging
# TODO: Hash body to catch edits
# TODO: Get running on server

try:
    CONFIG = yaml.full_load(open("config.yaml", 'r'))
    print("Config file loaded!")
except Exception as e:
    print("Error loading config file: {}".format(e))
    exit()

redis_cache = redis.Redis(host='redis', port=6379)

try:
    redis_cache.ping()
    print("Redis connection successfully verified!")
except Exception as e:
    print("Error connecting to Redis: {}".format(e))
    exit()


def get_submission_hits(sub_id):
    retries = 5
    while True:
        try:
            return redis_cache.incr(sub_id)
        except redis.exceptions.ConnectionError as exc:
            if retries == 0:
                raise exc
            retries -= 1
            time.sleep(0.5)


prowl = pyprowl.Prowl(os.environ.get('PROWL_API_KEY'))

try:
    prowl.verify_key()
    print("Prowl API key successfully verified!")
except Exception as e:
    print("Error verifying Prowl API key: {}".format(e))
    exit()


def notify_event(url, subreddit, desc):
    try:
        prowl.notify(event='Hit', description=desc, priority=0, appName='subredmonitor', url=url)
        print("Notification successfully sent to Prowl!")
    except Exception as e:
        print("Error sending notification to Prowl: {}".format(e))


reddit = praw.Reddit(
    client_id=os.environ.get('REDDIT_CLIENT_ID'),
    client_secret=os.environ.get('REDDIT_CLIENT_SECRET'),
    password=os.environ.get('REDDIT_PASSWORD'),
    user_agent="subredmonitor v0.1",
    username=os.environ.get('REDDIT_USERNAME'),
)

if type(CONFIG['subreddit']) == list :
    SUBREDDITS = '+'.join(CONFIG['subreddit'])
elif type(CONFIG['subreddit']) == str :
    SUBREDDITS = CONFIG['subreddit']
else:
    print("Config option 'subreddit' is invalid type: " + str(type(CONFIG['subreddit'])))
    exit()

print('Monitoring {}'.format(SUBREDDITS))

for submission in reddit.subreddit(SUBREDDITS).stream.submissions():
    text_matched, title_matched = False, False
    for title_match in CONFIG['title_matches']:
        if submission.title.lower().find(title_match.lower()) != -1:
            title_matched = True
    if title_matched:
        if reddit.submission(submission.id).is_self:
            text = reddit.submission(submission.id).selftext
            for text_match in CONFIG['text_matches']:
                if text.lower().find(text_match.lower()) != -1:
                    text_matched = True
            if text_matched:
                hits = get_submission_hits(submission.id)
                print('/u/{}: ({}) {}'.format(submission.author.name,
                                              submission.id, submission.title))
                if hits > 1:
                    print(
                        "Skipping notification because we've seen this {} times.".format(hits))
                else:
                    notify_event(submission.url, SUBREDDITS, submission.title)
