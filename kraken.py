import logging
import krakenex


class Kraken:
    def __init__(self):
        self.k = krakenex.API()
        self.k.load_key('kraken.key')

    def create_new_order(self, pair, type, ordertype, volume, leverage, price=0):

        # create an order
        order_data = {'pair': pair,
                      'type': type,
                      'ordertype': ordertype,
                      'volume': volume,
                      'leverage': leverage}
        if price > 0:
            order_data["price"] = price

        # execute order
        result = self.k.query_private('AddOrder', order_data)

        # parse result
        dict(result)

        if result['error']:
            for e in result['error']:
                logging.error("Creating new order failed: " + e)
            return {}
        else:
            logging.info("Created order: " + result['result']['descr']['order'] + " [" + result['result']['txid'][0] + "]")
            return result['result']

    def get_open_positions(self, pair=None, ordertxid=None):
        # prepare request
        req_data = {'docalcs': 'true'}
        if pair:
            req_data['pair'] = pair
        if ordertxid:
            req_data['ordertxid'] = ordertxid

        # query servers
        result = self.k.query_private('OpenPositions', req_data)

        # parse result
        dict(result)

        if result['error']:
            for e in result['error']:
                logging.error("Getting open positions failed: " + e)
            return {}
        else:
            rr = []
            if pair:
                # we are searching for a specific pair
                for id in result['result']:
                    if result['result'][id]['pair'] == pair:
                        rr.append(result['result'][id])
                        return rr
                # we didn't find that pair
                if not rr:
                    return {}
            return result['result']

    def get_open_orders(self, txid=None):
        # prepare request
        req_data = {'docalcs': 'true'}
        if txid:
            req_data['refid'] = txid

        # query servers
        result = self.k.query_private('OpenOrders', req_data)

        # parse result
        dict(result)

        if result['error']:
            for e in result['error']:
                logging.error("Getting open orders failed: " + e)
            return {}
        else:
            return result['result']['open']

    def get_balance(self):

        r = self.k.query_private('Balance')

        if r["error"]:
            logging.error("Getting balance failed: " + r["error"])
            return {}
        else:
            return r["result"]
