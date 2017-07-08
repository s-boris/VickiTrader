import logging
import krakenex


class Kraken:
    def __init__(self):
        self.k = krakenex.API()
        self.k.load_key('kraken.key')

    def create_new_order(self, pair, type, ordertype, volume, leverage, price=0):
        order_data = {'pair': pair,
                      'type': type,
                      'ordertype': ordertype,
                      'volume': volume,
                      'leverage': leverage}
        if price > 0:
            order_data["price"] = price

        result = self.k.query_private('AddOrder', order_data)
        dict(result)

        if result['error']:
            for e in result['error']:
                logging.error("Creating new order failed: " + e)
            return {}
        else:
            logging.info("Created order: " + result['result']['descr']['order'] + " [" + result['result']['txid'][0] + "]")
            return result['result']

    def cancel_order(self, txid):
        req_data = {'txid': txid}
        result = self.k.query_private('CancelOrder', req_data)
        dict(result)

        if result['error']:
            for e in result['error']:
                logging.error("Canceling order [" + txid + "] failed: " + e)
            return {}
        else:
            return result['result']

    def get_open_positions(self, pair=None, ordertxid=None):
        req_data = {'docalcs': 'true'}
        if pair:
            req_data['pair'] = pair
        if ordertxid:
            req_data['ordertxid'] = ordertxid

        result = self.k.query_private('OpenPositions', req_data)
        dict(result)

        if result['error']:
            for e in result['error']:
                logging.error("Fetching open positions failed: " + e)
            return {}
        else:
            found = []
            if pair:
                # we are searching for a specific pair
                for id in result['result']:
                    if result['result'][id]['pair'] == pair:
                        found.append(result['result'][id])
                        return found
                # we didn't find that pair
                if not found:
                    return {}
            return result['result']

    def get_open_orders(self, txid=None):
        req_data = {'docalcs': 'true'}
        if txid:
            req_data['refid'] = txid

        result = self.k.query_private('OpenOrders', req_data)
        dict(result)

        if result['error']:
            for e in result['error']:
                logging.error("Fetching open orders failed: " + e)
            return {}
        else:
            return result['result']['open']

    def get_balance(self):
        r = self.k.query_private('Balance')
        if r["error"]:
            logging.error("Fetching balance failed: " + r["error"])
            return {}
        else:
            return r["result"]

    def get_ticker(self, pair):
        req_data = {'pair': pair}
        r = self.k.query_public('Ticker', req_data)
        if r["error"]:
            for e in r['error']:
                logging.error("Fetching ticker failed: " + e)
            return {}
        else:
            return r["result"]
