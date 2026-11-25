int g(void){return 1;}
int main(void) {
#ifdef DBG
  return g();
#endif
  return 0;
}
