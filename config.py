# Adjust these values
#
# vickipair = pair format used by vicki
# krakenpair = pair format used by kraken (X in front for cryptos, Z in front for fiat)
# betvolume = part of your balance that should be used for trades (0 = 0% and 1 = 100%)
# leverage = how much the betvolume should be leveraged (1-3 or 1-5 are available, depending on pair, check kraken)
#
# All your source currencies together should not be more than 1 in total (like in the default setting)
# ----------------------------------------------------------------
PAIRCONFIG = {'ETH/BTC': {'vickipair': 'ETHBTC',
                          'krakenpair': 'XETHXXBT',
                          'betvolume': 0.5,
                          'leverage': 5},

              'XMR/BTC': {'vickipair': 'XMRBTC',
                          'krakenpair': 'XXMRXXBT',
                          'betvolume': 0.7,
                          'leverage': 3},

              # 'LTC/BTC': {'vickipair': 'LTCBTC',
              #            'krakenpair': 'XLTCXXBT',
              #            'betvolume': 0.7,
              #            'leverage': 3},

              'ETH/USD': {'vickipair': 'ETHUSD',
                          'krakenpair': 'XETHZUSD',
                          'betvolume': 0.5,
                          'leverage': 5},
              }
# ----------------------------------------------------------------
