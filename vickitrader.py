import socket
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
        r = self.twitter_api.request('statuses/user_timeline', {'user_id': 834940874643615744})
        response = json.loads(r.text)
        if len(response) >= 1:
            return response[:5]
        else:
            logging.debug("Error: No tweet found")
            return {}

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

    def orders_being_processed(self, pair):
        # make sure we are not processing an opening order on this pair right now
        for ao in self.appdata['awaiting_open']:
            if ao == pair:
                return True
        # make sure we are not processing a closing order on this pair right now
        for ac in self.appdata['awaiting_close']:
            if ac == pair:
                return True
        return False

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

    def close_positions_if_necessary(self, pair, type):
        # get opposite orders we might have to close first
        open_volume = self.get_position_volume(pair, type)

        if open_volume:
            # we need to close the open positions first by countering them
            logging.info("Closing open " + type + " positions on " + pair + "...")
            r = self.k.create_new_order(PAIRCONFIG[pair]['krakenpair'],
                                        type,
                                        ORDER_TYPE,
                                        str(open_volume),
                                        str(PAIRCONFIG[pair]['leverage']))
            if 'txid' in r and r['txid']:
                r['vol'] = open_volume
                self.appdata['awaiting_close'].append({"pair": pair, "type": type, "order": r})
            else:
                logging.error("Kraken was not able to execute the close order:(")
            return True
        return False

    def execute_swing(self, pair, type):
        try:
            # check if there are orders being processed on this pair
            if self.orders_being_processed(pair):
                return False

            if type == "sell":
                opposite_type = "buy"
            else:
                opposite_type = "sell"

            if self.close_positions_if_necessary(pair, opposite_type):
                # we started a position close. wait maximum 5 minutes for it to finish
                i = 0
                while i < 30:
                    time.sleep(10)
                    i += 1
                    self.refresh_state()
                    if not self.orders_being_processed(pair):
                        if self.close_positions_if_necessary(pair, opposite_type):
                            # close order disappeared but the position is still there... fuck this
                            return False
                        else:
                            # position has been closed, lets continue
                            break

            # we made sure there is no open positions or orders. we can just open our new position now
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
                    "It seems you have neither" + p_key + " nor " + s_key + " on kraken. Please deposit or buy some if you want to trade this pair.")
                return False

            r = self.k.create_new_order(PAIRCONFIG[pair]['krakenpair'],
                                        type,
                                        ORDER_TYPE,
                                        str(volume_to_order),
                                        str(PAIRCONFIG[pair]['leverage']))

            if 'txid' in r and r['txid']:
                r['vol'] = volume_to_order
                self.appdata['awaiting_open'].append(r)
                json.dump(self.appdata, open(APPDATA_FILE, 'w'))
                return True
            else:
                logging.error("Kraken was not able to execute the open order :(")
                return False

        except socket.timeout:
            logging.debug("Socked timed out, trying later...")

    def on_new_tweet(self, tweet):
        logging.info(tweet['user']['screen_name'] + ": " + tweet['text'])
        p_tweet = self.parse_tweet(tweet['text'])

        if p_tweet['type'] and p_tweet['pair']:
            logging.info("Detected " + p_tweet['type'] + " call on " + p_tweet['pair'] + ". Executing swing...")
            r = self.execute_swing(p_tweet['pair'], p_tweet['type'])
            if r:
                logging.info("Swing of " + p_tweet['pair'] + " to " + p_tweet['type'] + " has been completed!")
            else:
                logging.error("Swing of " + p_tweet['pair'] + " to " + p_tweet['type'] + " failed! :(")
        else:
            logging.info("Nothing interesting detected in this tweet")

        self.appdata['processed_tweets'].append(tweet['id'])

    def refresh_state(self):
        # Check if we are waiting for a position to open
        for ao in self.appdata['awaiting_open']:
            k_open_pos = self.k.get_open_positions(ordertxid=ao['txid'][0])
            k_open_ord = self.k.get_open_orders(txid=ao['txid'][0])
            found_open_vol = 0

            if ao['txid'][0] not in k_open_ord:
                for pos in k_open_pos:
                    if k_open_pos[pos]['ordertxid'] == ao['txid'][0]:
                        found_open_vol += pos['vol']
                if found_open_vol == ao['vol']:
                    logging.info("Order [" + ao['txid'][0] + "] (Volume: " + str(ao['vol']) + ") has been fulfilled")
                    self.appdata['awaiting_open'].remove(ao)
                elif found_open_vol != 0:
                    logging.info(
                        "Order [" + ao['txid'][0] + "] has been partially fulfilled (Volume: " + str(found_open_vol) + "/" + str(ao['vol']) + ")")
                else:
                    logging.warning("Order [" + ao['txid'][0] + "] disappeared or has not completely filled. Please check on Kraken...")
                    self.appdata['awaiting_open'].remove(ao)

        # Check if we are waiting for a position to close
        for ac in self.appdata['awaiting_close']:
            k_close_pos = self.k.get_open_positions(ordertxid=ac['order']['txid'][0])
            k_close_ord = self.k.get_open_orders(txid=ac['order']['txid'][0])
            found_close_vol = False

            if not k_close_ord and not k_close_pos:
                for pos in k_close_pos:
                    if k_close_pos[pos]['ordertxid'] == ac['order']['txid'][0]:
                        found_close_vol += ac['order']['vol']
                if found_close_vol == ac['order']['vol']:
                    logging.info("Order [" + ac['order']['txid'][0] + "] (Volume: " + str(ac['order']['vol']) + ") has been fulfilled")
                    self.appdata['awaiting_close'].remove(ac)
                elif found_close_vol != 0:
                    logging.info(
                        "Order [" + ac['order']['txid'][0] + "] has been partially fulfilled (Volume: " + str(found_close_vol) + "/" + str(
                            ac['order']['vol']) + ")")
                else:
                    logging.warning(
                        "Order [" + ac['order']['txid'][0] + "] disappeared without opening a position?! Guess we wait for the next call :/")
                    self.appdata['awaiting_close'].remove(ac)

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
