#!/usr/bin/env python3
"""Monitor a Subreddit for posts and send notifications"""

import logging
from os import environ
import time

import praw
import pyprowl
import redis
import requests
import yaml

# TODO: Hash body to catch edits

LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt=DATE_FORMAT)

try:
    with open("config.yaml", 'r', encoding="utf-8") as config_file:
        CONFIG = yaml.full_load(config_file)
    logging.info("Config file loaded!")
except Exception as e:
    logging.critical("Error loading config file: %s", e)
    exit()

log_level = CONFIG['log_level'] if 'log_level' in CONFIG else 'INFO'
logger = logging.getLogger()

try:
    logger.setLevel(log_level)
except Exception as e:
    logging.critical("Error setting log level: %s", e)
    exit()

redis_cache = redis.Redis(host='redis', port=6379)

try:
    redis_cache.ping()
    logging.info("Redis connection successfully verified!")
except Exception as e:
    logging.critical("Error connecting to Redis: %s", e)
    exit()


def get_submission_hits(sub_id):
    """Return the number of times a submission was seen according to Redis"""
    retries = 5
    while True:
        try:
            return redis_cache.incr(sub_id)
        except redis.exceptions.ConnectionError as exc:
            if retries == 0:
                raise exc
            retries -= 1
            time.sleep(0.5)


def are_prowl_credentials_valid():
    """Gets the Prowkl credentials from the environment and tests them against the API"""
    valid = False
    if 'PROWL_API_KEY' in environ:
        try:
            prowl = pyprowl.Prowl(environ['PROWL_API_KEY'])
            prowl.verify_key()
            logging.info("Prowl API key successfully verified!")
            valid = True
        except Exception as err:
            logging.critical("Error verifying Prowl API key: %s", err)
    return valid


def are_pushover_credentials_valid():
    """Gets the Pushover credentials from the environment and tests them against the API"""
    valid = False
    if 'PUSHOVER_APP_TOKEN' in environ and 'PUSHOVER_USER_KEY' in environ:
        post_data = {"token": environ['PUSHOVER_APP_TOKEN'],
                     "user": environ['PUSHOVER_USER_KEY']}
        try:
            response = requests.post(
                "https://api.pushover.net/1/users/validate.json", timeout=5, data=post_data)
        except Exception as err:
            logging.critical("Error verifying Pushover credentials: %s", err)

        if response.ok:
            logging.info("Pushover credentials successfully verified!")
            valid = True
        else:
            logging.error("Error verifying Pushover credentials: %s: %s",
                          response.status_code, response.reason)
    return valid

def validate_and_return_praw():
    """Validate the Reddit credentials and return a PRAW object"""
    envvars = ['REDDIT_USERNAME',
               'REDDIT_PASSWORD',
               'REDDIT_CLIENT_ID',
               'REDDIT_CLIENT_SECRET']
    if set(envvars) <= environ.keys():
        username = environ['REDDIT_USERNAME']
        reddit = praw.Reddit(
            client_id=environ['REDDIT_CLIENT_ID'],
            client_secret=environ['REDDIT_CLIENT_SECRET'],
            password=environ['REDDIT_PASSWORD'],
            user_agent=f'Python:subredmonitor:v0.1 (by /u/{username})',
            username=username,
        )
        try:
            reddit.user.me()
        except Exception as err:
            logging.error("Error validating Reddit credentials: %s", err)
            exit()
    else:
        logging.error("Reddit credentials missing: %s", envvars)
        exit()

    return reddit

def notify_event_pushover(url, message):
    """Send a notification to Pushover"""
    if PUSHOVER_ENABLED:
        post_data = {"token": environ['PUSHOVER_APP_TOKEN'],
                     "user": environ['PUSHOVER_USER_KEY'],
                     "message": message, "url": url,
                     "url_title": message}
        try:
            response = requests.post(
                "https://api.pushover.net/1/messages.json", timeout=5, data=post_data)
        except Exception as err:
            logging.error("Error sending notification to Pushover: %s", err)

        if response.ok:
            logging.info("Notification successfully sent to Pushover!")
        else:
            logging.error("Error sending notification to Pushover: %s: %s",
                          response.status_code, response.reason)


def notify_event_prowl(url, description):
    """Send a notification to Prowl"""
    if PROWL_ENABLED:
        try:
            prowl = pyprowl.Prowl(environ['PROWL_API_KEY'])
            prowl.notify(event='Hit', description=description, priority=0,
                         appName='subredmonitor', url=url)
            logging.info("Notification successfully sent to Prowl!")
        except Exception as err:
            logging.error("Error sending notification to Prowl: %s", err)


def notify_event(url, subreddit, desc):
    """Send notifications"""
    body = f"{subreddit}: {desc}"
    notify_event_prowl(url, body)
    notify_event_pushover(url, body)

PRAW = validate_and_return_praw()
PROWL_ENABLED = are_prowl_credentials_valid()
PUSHOVER_ENABLED = are_pushover_credentials_valid()

if isinstance(CONFIG['subreddit'], list):
    SUBREDDITS = '+'.join(CONFIG['subreddit'])
elif isinstance(CONFIG['subreddit'], str):
    SUBREDDITS = CONFIG['subreddit']
else:
    logging.critical(
        "Config option 'subreddit' is invalid type: %s", str(type(CONFIG['subreddit'])))
    exit()

logging.info('Start monitoring: %s', SUBREDDITS)

for submission in PRAW.subreddit(SUBREDDITS).stream.submissions():
    text_matched, title_matched = False, False
    # search for titles
    for title_match in CONFIG['title_match']:
        if submission.title.lower().find(title_match.lower()) != -1:
            # once title found search for secondary if definded
            if 'title_match_secondary' in CONFIG:
                for title_match_secondary in CONFIG['title_match_secondary']:
                    if submission.title.lower().find(title_match_secondary.lower()) != -1:
                        title_matched = True
            else:
                title_matched = True
    if title_matched:
        # search for submission text if defined
        if 'text_match' in CONFIG:
            if PRAW.submission(submission.id).is_self:
                text = PRAW.submission(submission.id).selftext
                for text_match in CONFIG['text_match']:
                    if text.lower().find(text_match.lower()) != -1:
                        text_matched = True
        else:
            text_matched = True
        if text_matched:
            hits = get_submission_hits(submission.id)
            logging.info('Matched submission from /u/%s: (%s) %s',
                         submission.author.name, submission.id, submission.title)
            if hits > 1:
                logging.info(
                    "Skipping notification because we've seen this %s times.", hits)
            else:
                notify_event(f'https://www.reddit.com{submission.permalink}',
                             SUBREDDITS, submission.title)
        else:
            logging.info('Title matched but text did not /u/%s: (%s) %s',
                         submission.author.name, submission.id, submission.title)
    else:
        logging.info('Title did not match /u/%s: (%s) %s',
                     submission.author.name, submission.id, submission.title)
