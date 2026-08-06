// Hop 1: the central URL module. Highest-confidence source of frontend->endpoint edges,
// because everything else in this component is supposed to import from here.
export const URLS = {
  ORDERS_LIST: '/orders/v1/list',
  ORDERS_DETAIL: '/orders/v1/detail/{orderId}',
};
