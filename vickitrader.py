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
        self.check_first_start(self.get_last_vicki_tweet())

        # configure Kraken API
        self.k = kraken.Kraken()

    def check_first_start(self, last_tweet):
        # check if this is the first start
        if not self.appdata['processed_tweets']:
            self.appdata['processed_tweets'].append(last_tweet['id'])
            json.dump(self.appdata, open(APPDATA_FILE, 'w'))

    def get_last_vicki_tweet(self):
        r = self.twitter_api.request('statuses/user_timeline', {'user_id': 834940874643615744})
        response = json.loads(r.text)
        if len(response) >= 1:
            last_tweet = response[0]
        else:
            logging.debug("Error: No tweet found")
            return {}
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

    def orders_being_processed(self, pair, type):
        # make sure we are not processing an opening order on this pair right now
        for ao in self.appdata['awaiting_open']:
            if ao == pair:
                logging.warning("Conflict! There is an opening order on " + pair + " but we are trying to " + type)
                logging.warning("Trying to cancel the opening order before placing a new one...")
                ro = self.k.cancel_order(ao['txid'][0])
                if ro:
                    logging.info("Opening order [" + ao['txid'][0] + "] has been canceled")
                    self.appdata['awaiting_open'].remove(ao)
                else:
                    logging.error("Could not cancel the current opening order... Guess we have to skip this call :/")
                    return True

        # make sure we are not processing a closing order on this pair right now
        for ac in self.appdata['awaiting_close']:
            if ac == pair:
                logging.warning("Conflict! There is a closing order on " + pair + " but we are trying to " + type)
                logging.warning("Trying to cancel the closing order before placing a new one...")
                ro = self.k.cancel_order(ac['txid'][0])
                if ro:
                    logging.info("Closing order [" + ac['txid'][0] + "] has been canceled")
                    self.appdata['awaiting_close'].remove(ac)
                else:
                    logging.error("Could not cancel the current closing order... Guess we have to skip this call :/")
                    return True
        return False

    def get_turn_factor(self, pair, type):
        TURN_FACTOR = 1
        OPEN_VOLUME = 0

        # if we already have an open opposite position on this pair we will have to close with 200%
        open_positions = self.k.get_open_positions(pair=PAIRCONFIG[pair]['krakenpair'])
        if open_positions:
            if len(open_positions) == 1:
                OPEN_VOLUME = open_positions[0]['vol']
                if open_positions[0]['type'] == type:
                    logging.warning("We are trying to place a " + type + " order on " + pair + " but there is already one (Volume: " + str(
                        OPEN_VOLUME) + "). Won't touch anything then :)")
                    return False
                else:
                    # there is an open opposite position available, that we can turn
                    TURN_FACTOR = 2
                    OPEN_VOLUME = open_positions[0]['vol']
            elif len(open_positions) > 1:
                # multiple orders are open already. accumulate the total volume
                for op in open_positions:
                    if op['type'] == type:
                        OPEN_VOLUME += op['vol']
                if OPEN_VOLUME != 0:
                    logging.warning(
                        "We are trying to place a " + type + " order on " + pair + " but there are already multiple orders open (Total volume: " + str(
                            OPEN_VOLUME) + "). Won't touch anything then :)")
                    return False
        return TURN_FACTOR, OPEN_VOLUME

    def execute_swing(self, pair, type):
        # check if the order we are trying to process is in conflict with another order that is being opened right now
        try:
            if self.orders_being_processed(pair, type):
                return False

            TURN_FACTOR, OPEN_VOLUME = self.get_turn_factor(pair, type)
            if not TURN_FACTOR:
                # it seems there is already an open position. No need to turn or place anything
                return True

            b = self.k.get_balance()
            b_key = PAIRCONFIG[pair]['krakenpair'][:4]
            b_key2 = PAIRCONFIG[pair]['krakenpair'][4:]

            if b_key in b:
                volume_to_order = float(b[b_key]) * PAIRCONFIG[pair]['betvolume'] * PAIRCONFIG[pair]['leverage'] * TURN_FACTOR
            elif b_key2 in b:
                r = self.k.get_ticker(PAIRCONFIG[pair]['krakenpair'])
                ask_price = float(r[PAIRCONFIG[pair]['krakenpair']]['a'][0])
                ask_conversion = 1 / ask_price
                bid_price = float(r[PAIRCONFIG[pair]['krakenpair']]['b'][0])
                bid_conversion = 1 / bid_price
                if type == "sell":
                    volume_to_order = float(b[b_key2]) * (bid_conversion * 0.9) * PAIRCONFIG[pair]['betvolume'] * PAIRCONFIG[pair][
                        'leverage'] * TURN_FACTOR
                else:
                    volume_to_order = float(b[b_key2]) * (ask_conversion * 0.9) * PAIRCONFIG[pair]['betvolume'] * PAIRCONFIG[pair][
                        'leverage'] * TURN_FACTOR
            else:
                logging.warning(
                    "It seems you have neither" + b_key + " not " + b_key2 + " on kraken. Please deposit or buy some if you want to trade this pair.")
                volume_to_order = float(b[b_key]) * PAIRCONFIG[pair]['betvolume'] * PAIRCONFIG[pair]['leverage'] * TURN_FACTOR

            if volume_to_order < OPEN_VOLUME:
                # the open volume is larger than the volume we want to turn. close it first and then open a normal 100% order
                r = self.k.create_new_order(PAIRCONFIG[pair]['krakenpair'],
                                            type,
                                            ORDER_TYPE,
                                            str(OPEN_VOLUME),
                                            str(PAIRCONFIG[pair]['leverage']))
                if TURN_FACTOR == 2:
                    volume_to_order = volume_to_order / 2
                if 'txid' not in r and not r['txid']:
                    logging.error("Could not close the open volume required for a swing... Skipping this one :/")
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
        except socket.timeout:
            logging.debug("Socked timed out, trying later...")

    def on_new_tweet(self, tweet):
        logging.info(tweet['user']['screen_name'] + ": " + tweet['text'])
        p_tweet = self.parse_tweet(tweet['text'])

        if p_tweet['type'] and p_tweet['pair']:
            logging.info("Detected " + p_tweet['type'] + " call on " + p_tweet['pair'] + ". Placing order...")
            self.execute_swing(p_tweet['pair'], p_tweet['type'])
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
            k_close_pos = self.k.get_open_positions(ordertxid=ac['txid'][0])
            k_close_ord = self.k.get_open_orders(txid=ac['txid'][0])
            found_close_vol = False

            if not k_close_ord and not k_close_pos:
                for pos in k_close_pos:
                    if k_close_pos[pos]['ordertxid'] == ac['txid'][0]:
                        found_close_vol += ac['vol']
                if found_close_vol == ac['vol']:
                    logging.info("Order [" + ac['txid'][0] + "] (Volume: " + str(ac['vol']) + ") has been fulfilled")
                    self.appdata['awaiting_close'].remove(ac)
                elif found_close_vol != 0:
                    logging.info(
                        "Order [" + ac['txid'][0] + "] has been partially fulfilled (Volume: " + str(found_close_vol) + "/" + str(ac['vol']) + ")")
                else:
                    logging.warning("Order [" + ac['txid'][0] + "] disappeared without opening a position?! Guess we wait for the next call :/")
                    self.appdata['awaiting_close'].remove(ac)

        json.dump(self.appdata, open(APPDATA_FILE, 'w'))

    def run(self):
        logging.info("Waiting for tweets...")
        while True:
            last_tweet = self.get_last_vicki_tweet()

            if last_tweet:
                # if this is a new tweet, process it
                if last_tweet['id'] not in self.appdata['processed_tweets']:
                    self.on_new_tweet(last_tweet)

                # refresh orders and positions
                self.refresh_state()
            time.sleep(20)


vt = VickiTrader()
vt.run()
