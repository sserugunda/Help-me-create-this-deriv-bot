const DerivTradingBot = require('./src/bot');
const config = require('./config.json');

const bot = new DerivTradingBot(config);

bot.start().catch(error => {
  console.error('Bot error:', error);
  process.exit(1);
});

process.on('SIGINT', () => {
  console.log('\nShutting down bot...');
  process.exit(0);
});
