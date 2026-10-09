"""Starting point: copy into s5validation and implement one agreed method."""
from s5validation.models import Outcome

class MyAdapter:
    def __init__(self,config,base):
        self.config=config
        self.base=base

    def validate(self,delivery,product_config,workdir):
        # Create revisioned artifact directories; read local product, apply QA,
        # collocate actual reference observations and preserve full evidence.
        return Outcome('PENDING_REFERENCE','Reference integration has not been implemented','NOT_AVAILABLE')
