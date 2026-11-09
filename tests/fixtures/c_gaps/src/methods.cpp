struct Counter {
    int kept;
    int read_gone() const { return gone_member; }
    int read_kept() const { return kept; }
};
int not_a_method() { return gone_member; }
