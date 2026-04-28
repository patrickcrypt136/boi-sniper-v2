from web3 import Web3

avax = Web3(Web3.HTTPProvider('https://api.avax.network/ext/bc/C/rpc'))
bsc = Web3(Web3.HTTPProvider('https://bsc-dataseed1.defibit.io'))

wallet = '0x3614F0fb1eFAE12343E22904B19D1B7bF040648B'

print('AVAX:', avax.from_wei(avax.eth.get_balance(wallet), 'ether'))
print('BSC:', bsc.from_wei(bsc.eth.get_balance(wallet), 'ether'))