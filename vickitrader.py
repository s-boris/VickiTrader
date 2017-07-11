import socket
import sys
import os.path
import time
import json
import logging
from json import JSONDecodeError

import kraken
from TwitterAPI import TwitterAPI, TwitterConnectionError

from config import PAIRCONFIG

ORDER_TYPE = "market"
APPDATA_FILE = "app.data"


class VickiTrader:
    def __init__(self):

        # init logger
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

        # create appdata
        if os.path.isfile(APPDATA_FILE):
            self.appdata = json.load(open(APPDATA_FILE))
        else:
            self.appdata = {'processed_tweets': [],
                            'awaiting_order': []}
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

        # init appdata
        self.check_first_start(self.get_vicki_tweets())

        # configure Kraken API
        self.k = kraken.Kraken()

    def check_first_start(self, last_tweets):
        # check if this is the first start
        if not self.appdata['processed_tweets']:
            for t in last_tweets:
                self.appdata['processed_tweets'].append(t['id'])
            json.dump(self.appdata, open(APPDATA_FILE, 'w'))

    def get_vicki_tweets(self):
        try:
            r = self.twitter_api.request('statuses/user_timeline', {'user_id': 834940874643615744})
            response = json.loads(r.text)
            if len(response) >= 1:
                return response[:5]
            else:
                logging.debug("Error: No tweet found")
                return {}
        except TwitterConnectionError:
            logging.debug("TwitterConnectionError - Reinitialize...")
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

    def on_new_tweet(self, tweet):
        logging.info(tweet['user']['screen_name'] + ": " + tweet['text'])
        p_tweet = self.parse_tweet(tweet['text'])

        if p_tweet['type'] and p_tweet['pair']:
            logging.info("Detected " + p_tweet['type'] + " call on " + p_tweet['pair'] + ". Executing swing...")
            self.execute_swing(p_tweet['pair'], p_tweet['type'])
        else:
            logging.info("Nothing interesting detected in this tweet")

        self.appdata['processed_tweets'].append(tweet['id'])

    def execute_swing(self, pair, type):
        try:
            # check if there are orders being processed on this pair
            op = self.awaiting_order(pair)
            if op:
                for o in op:
                    # try to close the orders so they don't interfere
                    self.k.cancel_order(o['txid'])

            # check if we already have an open position with this type. If so, just let it be
            open_this_volume = self.get_position_volume(pair, type)
            if open_this_volume > 0:
                logging.warning(
                    "There seems to be a " + type + " position of " + str(round(open_this_volume, 5)) + " open already. Won't touch anything.")
                return True

            # we made sure no orders are being processed and no position of the same type is open already
            # if this is a swing, we need to add the amount needed to close it to our new swing order
            if type == "sell":
                opp_type = "buy"
            else:
                opp_type = "sell"
            open_opp_volume = self.get_position_volume(pair, opp_type)

            # calculate the new volume we want to order
            b = self.k.get_balance()
            p_key = PAIRCONFIG[pair]['krakenpair'][:4]
            s_key = PAIRCONFIG[pair]['krakenpair'][4:]

            if p_key in b:
                volume_to_order = float(b[p_key]) * PAIRCONFIG[pair]['betvolume'] * PAIRCONFIG[pair]['leverage']
            elif s_key in b:
                # seems like we have no funds in the primary currency. let's calculate how much we can order based on the secondary currency
                logging.info(
                    "You do not have any " + p_key + " funds. I'll try to calculate how much we can place based on your funds in the second currency (" + s_key + ")")
                r = self.k.get_ticker(PAIRCONFIG[pair]['krakenpair'])
                ask_price = float(r[PAIRCONFIG[pair]['krakenpair']]['a'][0])
                ask_conversion = 1 / ask_price
                bid_price = float(r[PAIRCONFIG[pair]['krakenpair']]['b'][0])
                bid_conversion = 1 / bid_price
                if type == "sell":
                    volume_to_order = float(b[s_key]) * (bid_conversion * 0.9) * PAIRCONFIG[pair]['betvolume'] * PAIRCONFIG[pair]['leverage']
                else:
                    volume_to_order = float(b[s_key]) * (ask_conversion * 0.9) * PAIRCONFIG[pair]['betvolume'] * PAIRCONFIG[pair]['leverage']
            else:
                logging.warning(
                    "It seems you have neither" + p_key + " nor " + s_key + " on Kraken. Please deposit or buy some if you want to trade this pair.")
                return False

            if open_opp_volume > 0:
                # we are swinging. we need to add the enough to close the opposite position
                logging.info("Open " + opp_type + " position on " + pair + " detected. Countering it with an additional volume of " + str(
                    round(open_opp_volume, 5)) + "...")
                volume_to_order += open_opp_volume

            r = self.k.create_new_order(PAIRCONFIG[pair]['krakenpair'],
                                        type,
                                        ORDER_TYPE,
                                        str(volume_to_order),
                                        str(PAIRCONFIG[pair]['leverage']))

            if 'txid' in r and r['txid']:
                r['vol'] = (volume_to_order - open_opp_volume)
                self.appdata['awaiting_order'].append(r)
                json.dump(self.appdata, open(APPDATA_FILE, 'w'))
                return True
            else:
                logging.error("Kraken was not able to execute the order :(")
                return False

        except socket.timeout:
            logging.debug("Socked timed out, trying later...")

    def awaiting_order(self, pair):
        found = []
        for ao in self.appdata['awaiting_order']:
            if ao == pair:
                found.append(ao)
                return ao
        return found

    def get_position_volume(self, pair, type):
        positions = []
        volume = 0

        open_positions = self.k.get_open_positions(pair=PAIRCONFIG[pair]['krakenpair'])

        if open_positions:
            # multiple positions are open already. accumulate the total volume
            for op in open_positions:
                if op['type'] == type:
                    volume += float(op['vol'])
                    positions.append(op)
            return volume
        else:
            return False

    def refresh_state(self):

        for ao in self.appdata['awaiting_order']:
            # Check if we are waiting for an order
            try:
                k_open_ords = self.k.get_open_orders()
                k_open_pos = self.k.get_open_positions(ordertxid=ao['txid'][0])
            except JSONDecodeError:
                logging.debug("JSONDecodeError - Trying again later...")
                return False
            found_open_vol = 0

            if ao['txid'][0] not in k_open_ords:
                # check if and how much has been opened already
                for pos in k_open_pos:
                    if pos['ordertxid'] == ao['txid'][0]:
                        found_open_vol += float(pos['vol'])

                if round(found_open_vol, 4) == round(ao['vol'], 4):
                    logging.info("Order [" + ao['txid'][0] + "] (Volume: " + str(round(ao['vol'], 4)) + ") has been fulfilled")
                    self.appdata['awaiting_order'].remove(ao)
                elif found_open_vol != 0:
                    logging.info(
                        "Order [" + ao['txid'][0] + "] has been partially fulfilled (Volume: " + str(round(found_open_vol, 4)) + "/" + str(
                            round(ao['vol'], 4)) + ")")
                else:
                    logging.warning("Order [" + ao['txid'][0] + "] disappeared or has not completely filled. Please check on Kraken...")
                    self.appdata['awaiting_order'].remove(ao)

        json.dump(self.appdata, open(APPDATA_FILE, 'w'))

    def run(self):
        logging.info("Waiting for tweets...")
        while True:
            tweets = self.get_vicki_tweets()
            scanned_tweets = 0

            if tweets:
                # scan the last 5 tweets (we assume the bot is not posting more than 5 tweets between our refreshes)
                for t in reversed(tweets):
                    scanned_tweets += 1
                    # if this is a new tweet, process it
                    if t['id'] not in self.appdata['processed_tweets']:
                        self.on_new_tweet(t)
                    if scanned_tweets >= 5:
                        break

            # refresh orders and positions
            self.refresh_state()
            time.sleep(20)


vt = VickiTrader()
vt.run()
