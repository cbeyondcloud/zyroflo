import { URLS } from '../config/urls';

export function OrderList() {
  return fetch(URLS.ORDERS_LIST).then((r) => r.json());
}
