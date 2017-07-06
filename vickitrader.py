import sys
import os.path
import time
import json
import logging

import kraken
from TwitterAPI import TwitterAPI

from config import PAIRCONFIG

ORDER_TYPE = "market"
APPDATA_FILE = "app.data"


class VickiTrader:
    def __init__(self):

        # setup logger
        root = logging.getLogger()
        root.setLevel(logging.DEBUG)
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        ch.setFormatter(formatter)
        root.addHandler(ch)
        fh = logging.FileHandler('log.txt')
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(formatter)
        root.addHandler(fh)

        # load appdata
        if os.path.isfile(APPDATA_FILE):
            self.appdata = json.load(open(APPDATA_FILE))
        else:
            self.appdata = {'processed_tweets': [],
                            'awaiting_open': [],
                            'awaiting_close': []}
            json.dump(self.appdata, open(APPDATA_FILE, 'w'))

        # configure Twitter API
        with open("twitter.key", 'r') as f:
            self.twitter_consumer_key = f.readline().strip()
            self.twitter_consumer_secret = f.readline().strip()
            self.twitter_access_token_key = f.readline().strip()
            self.twitter_access_token_secret = f.readline().strip()

        self.twitter_api = TwitterAPI(self.twitter_consumer_key,
                                      self.twitter_consumer_secret,
                                      self.twitter_access_token_key,
                                      self.twitter_access_token_secret)

        # configure Kraken API
        self.k = kraken.Kraken()

        logging.info("Waiting for tweets...")
        self.run()

    def check_first_start(self, last_tweet):
        # check if this is the first start
        if not self.appdata['processed_tweets']:
            self.appdata['processed_tweets'].append(last_tweet['id'])
            json.dump(self.appdata, open(APPDATA_FILE, 'w'))

    def get_last_vicki_tweet(self):
        r = self.twitter_api.request('statuses/user_timeline', {'user_id': 834940874643615744})
        response = json.loads(r.text)
        last_tweet = response[6]
        return last_tweet

    def parse_tweet(self, tweet):
        result = {'type': '', 'pair': ''}

        if "short" in tweet.lower():
            result['type'] = 'sell'
        elif "long" in tweet.lower():
            result['type'] = 'buy'

        for p in PAIRCONFIG:
            if PAIRCONFIG[p]['vickipair'] in tweet:
                result['pair'] = p

        return result

    def execute_swing(self, pair, type):
        TURN_FACTOR = 1

        # make sure we are not processing an order open/close on this pair right now
        for ao in self.appdata['awaiting_open']:
            if ao == pair:
                # TODO handle this
                logging.error("Conflict! There is an opening order on " + pair + " but we are trying to " + type)
        for ac in self.appdata['awaiting_close']:
            if ac == pair:
                # TODO handle this
                logging.error("Conflict! There is a closing order on " + pair + " but we are trying to " + type)

        # if we already have an open opposite position on this pair we will have to close with 200%
        open_positions = self.k.get_open_positions(pair=PAIRCONFIG[pair]['krakenpair'])
        if open_positions:
            if len(open_positions) == 1:
                if open_positions[0]['type'] == type:
                    # TODO handle this
                    logging.error('We are trying to place a ' + type + ' order on ' + pair + ' but there is already one!')
                    return False
                else:
                    TURN_FACTOR = 2
            elif len(open_positions) > 1:
                # TODO check what volume is open
                logging.warning("The order was split into multiple... TODO")
                return False

        b = self.k.get_balance()
        b_key = PAIRCONFIG[pair]['krakenpair'][:4]
        effective_volume = float(b[b_key]) * PAIRCONFIG[pair]['betvolume'] * PAIRCONFIG[pair][
            'leverage'] * TURN_FACTOR  # balance * betvolume * leverage * turn

        r = self.k.create_new_order(PAIRCONFIG[pair]['krakenpair'],
                                    type,
                                    ORDER_TYPE,
                                    str(effective_volume),
                                    str(PAIRCONFIG[pair]['leverage']))

        if 'txid' in r and r['txid']:
            self.appdata['awaiting_open'].append(r)
            json.dump(self.appdata, open(APPDATA_FILE, 'w'))

    def on_new_tweet(self, tweet):
        logging.info(tweet['user']['screen_name'] + ": " + tweet['text'])
        p_tweet = self.parse_tweet(tweet['text'])

        if p_tweet['type'] and p_tweet['pair']:
            logging.info("Detected " + p_tweet['type'] + " call on " + p_tweet['pair'] + ", placing order...")
            self.execute_swing(p_tweet['pair'], p_tweet['type'])

        else:
            logging.info("...nothing interesting detected in this tweet")
        self.appdata['processed_tweets'].append(tweet['id'])

    def refresh_state(self):

        for ao in self.appdata['awaiting_open']:
            # Check if we are waiting for a position to open
            k_open_pos = self.k.get_open_positions(ordertxid=ao['txid'][0])
            k_open_ord = self.k.get_open_orders(txid=ao['txid'][0])
            found = False

            # TODO check if volume is complete
            if ao['txid'][0] not in k_open_ord:
                for pos in k_open_pos:
                    if k_open_pos[pos]['ordertxid'] == ao['txid'][0]:
                        found = True
                if found:
                    logging.info("Order [" + ao['txid'][0] + "] has been fulfilled")
                    self.appdata['awaiting_open'].remove(ao)
                else:
                    logging.warning("Order [" + ao['txid'][0] + "] disappeared without opening a position?!")

        for ac in self.appdata['awaiting_close']:
            # Check if we are waiting for a position to close
            k_open_pos = self.k.get_open_positions(ordertxid=ac['txid'][0])
            k_open_ord = self.k.get_open_orders(txid=ac['txid'][0])
            found = False

            # TODO check if volume is complete
            if not k_open_ord and not k_open_pos:
                for pos in k_open_pos:
                    if k_open_pos[pos]['ordertxid'] == ac['txid'][0]:
                        found = True

                if found:
                    logging.info("Order [" + ac['txid'][0] + "] has been fulfilled")
                    self.appdata['awaiting_close'].remove(ac)
                else:
                    logging.warning("Order [" + ac['txid'][0] + "] disappeared without closing a position?!")

        json.dump(self.appdata, open(APPDATA_FILE, 'w'))

    def run(self):
        # create appdata if we just started
        # self.check_first_start(self.get_last_vicki_tweet())

        while True:
            last_tweet = self.get_last_vicki_tweet()

            # if this is a new tweet, process it
            if not last_tweet['id'] in self.appdata['processed_tweets']:
                self.on_new_tweet(last_tweet)

            # refresh orders and positions
            self.refresh_state()
            time.sleep(15)


vt = VickiTrader()
