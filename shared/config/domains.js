/**
 * ONDC Domain Code Mappings
 */

const DOMAINS = {
  // Retail
  GROCERY: 'ONDC:RET10',
  FOOD_AND_BEVERAGE: 'ONDC:RET11',
  FASHION: 'ONDC:RET12',
  BEAUTY_AND_PERSONAL_CARE: 'ONDC:RET13',
  ELECTRONICS: 'ONDC:RET14',
  HOME_AND_DECOR: 'ONDC:RET15',
  HEALTH_AND_WELLNESS: 'ONDC:RET16',
  AGRICULTURE: 'ONDC:RET17',
  TOYS_AND_GAMES: 'ONDC:RET18',
  MISCELLANEOUS: 'ONDC:RET19',

  // Other domains
  LOGISTICS: 'ONDC:LOG',
  FINANCIAL_SERVICES: 'ONDC:FIS',
  MOBILITY: 'ONDC:MBL',
  SERVICES: 'ONDC:SRV',
};

const ACTIONS = [
  'search', 'select', 'init', 'confirm', 'status',
  'track', 'cancel', 'update', 'rating', 'support',
];

const CALLBACKS = ACTIONS.map(action => `on_${action}`);

const CORE_VERSION = '1.2.0';
const COUNTRY = 'IND';

module.exports = {
  DOMAINS,
  ACTIONS,
  CALLBACKS,
  CORE_VERSION,
  COUNTRY,
};
