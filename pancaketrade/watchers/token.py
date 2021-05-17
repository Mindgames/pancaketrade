"""Token watcher."""
from typing import List, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger
from pancaketrade.network import Network
from pancaketrade.persistence import Token
from pancaketrade.watchers.order import OrderWatcher
from pancaketrade.utils.config import Config
from telegram.ext import Dispatcher
from web3 import Web3


class TokenWatcher:
    def __init__(
        self,
        token_record: Token,
        net: Network,
        dispatcher: Dispatcher,
        config: Config,
        orders: List = list(),
    ):
        self.net = net
        self.dispatcher = dispatcher
        self.config = config
        self.token_record = token_record
        self.address = Web3.toChecksumAddress(token_record.address)
        self.decimals = int(token_record.decimals)
        self.symbol = str(token_record.symbol)
        emoji = token_record.icon + ' ' if token_record.icon else ''
        self.name = emoji + self.symbol
        self.default_slippage = token_record.default_slippage
        self.orders: List[OrderWatcher] = [
            OrderWatcher(
                order_record=order_record,
                net=self.net,
                dispatcher=self.dispatcher,
                chat_id=self.config.secrets.admin_chat_id,
            )
            for order_record in orders
        ]
        self.interval = self.config.monitor_interval
        self.scheduler = BackgroundScheduler(
            job_defaults={'coalesce': True, 'max_instances': 1, 'misfire_grace_time': 0.8 * self.interval}
        )
        self.last_status_message_id: Optional[int] = None
        self.start_monitoring()

    def start_monitoring(self):
        trigger = IntervalTrigger(seconds=self.interval)
        self.scheduler.add_job(self.monitor_price, trigger=trigger)
        self.scheduler.start()

    def monitor_price(self):
        if not self.orders:
            return
        sell_price, sell_v2 = self.net.get_token_price(
            token_address=self.address, token_decimals=self.decimals, sell=True
        )
        if self.net.has_both_versions(token_address=self.address):
            buy_price, buy_v2 = self.net.get_token_price(
                token_address=self.address, token_decimals=self.decimals, sell=False
            )
        else:
            buy_price = sell_price
            buy_v2 = sell_v2
        indices_to_remove: List[int] = []
        for i, order in enumerate(self.orders):
            if order.finished:
                indices_to_remove.append(i)
                continue
            v2 = buy_v2 if order.type == 'buy' else sell_v2
            if not self.net.is_approved(token_address=self.address, v2=v2):
                version = 'v2' if v2 else 'v1'
                logger.info(f'Need to approve {self.symbol} for trading on PancakeSwap {version}.')
                self.dispatcher.bot.send_message(
                    chat_id=self.config.secrets.admin_chat_id,
                    text=f'Approving {self.symbol} for trading on PancakeSwap {version}...',
                )
                res = self.net.approve(token_address=self.address, v2=v2)
                if res:
                    self.dispatcher.bot.send_message(
                        chat_id=self.config.secrets.admin_chat_id,
                        text='✅ Approval successful!',
                    )
                else:
                    self.dispatcher.bot.send_message(
                        chat_id=self.config.secrets.admin_chat_id,
                        text='⛔ Approval failed',
                    )
            order.price_update(sell_price=sell_price, buy_price=buy_price, sell_v2=sell_v2, buy_v2=buy_v2)
        self.orders = [o for i, o in enumerate(self.orders) if i not in indices_to_remove]
