import java.sql.*;
import java.util.*;
import java.io.*;

/* HSQLDB 2.7.4 implementation of the RQ7 replay.
 * Protocol constants and workload construction are inherited verbatim from
 * scripts/sqlite_replay.py for the controlled RQ7 workload. */
public final class HsqldbReplay {
  static final long SEED=20260923L; static final int N=64,T=2048,W=768,Q=120,RUNS=3,REPS=4;
  static final int[] PAYLOADS={256,1024,4096}; static final int[] QUERY={0,1,2,3,4,5,6,7};
  static final List<int[]> STATIC=new ArrayList<>(), TEMPORAL=new ArrayList<>();
  static final Map<Integer,List<Integer>> UPDATES=new HashMap<>();
  static { int[] cold=new int[48]; for(int i=0;i<48;i++) cold[i]=16+i;
    for(int i=0;i<8;i++){int[] a=new int[8]; a[0]=2*i;a[1]=2*i+1;for(int j=0;j<6;j++)a[j+2]=cold[6*i+j];STATIC.add(a);}
    for(int i=0;i<8;i++) TEMPORAL.add(new int[]{2*i,2*i+1});
    for(int i=0;i<8;i++){int[] a=new int[6];for(int j=0;j<6;j++)a[j]=cold[6*i+j];TEMPORAL.add(a);}
    for(int a=0;a<N;a++){ArrayList<Integer> u=new ArrayList<>(); if(a<16){int phase=((a/2)*11)%96+16;for(int t=phase;t<T;t+=128)u.add(t);}else{int phase=(a*29)%64+4, period=64+(a%3)*16;for(int t=phase;t<T;t+=period)u.add(t);}UPDATES.put(a,u);}
  }
  static Connection open(String id) throws Exception { return DriverManager.getConnection("jdbc:hsqldb:mem:"+id+";shutdown=true","SA",""); }
  static void exec(Connection c,String q)throws Exception{try(Statement s=c.createStatement()){s.execute(q);}}
  static void setup(Connection c,int pb)throws Exception{
    exec(c,"SET FILES LOG FALSE"); exec(c,"CREATE TABLE fragment_atoms(layout VARCHAR(16) NOT NULL, fragment_id INTEGER NOT NULL, atom_id INTEGER NOT NULL)");
    exec(c,"CREATE INDEX idx_fa ON fragment_atoms(layout,atom_id,fragment_id)"); exec(c,"CREATE TABLE versions(layout VARCHAR(16) NOT NULL, fragment_id INTEGER NOT NULL, valid_from INTEGER NOT NULL, valid_to INTEGER NOT NULL, payload VARBINARY(4096) NOT NULL)"); exec(c,"CREATE INDEX idx_v ON versions(layout,fragment_id,valid_from,valid_to)");
    exec(c,"CREATE TABLE signal(atom_id INTEGER NOT NULL,t INTEGER NOT NULL,value DOUBLE NOT NULL)");exec(c,"CREATE INDEX idx_sig ON signal(atom_id,t)");
    try(PreparedStatement p=c.prepareStatement("INSERT INTO signal VALUES(?,?,?)")){for(int a=0;a<16;a++)for(int t=0;t<T;t++){p.setInt(1,a);p.setInt(2,t);p.setDouble(3,Math.sin(.13*a+.017*t)+.1*Math.cos(.031*t));p.addBatch();}p.executeBatch();}
    addLayout(c,"static",STATIC,pb);addLayout(c,"temporal",TEMPORAL,pb);c.commit();
  }
  static void addLayout(Connection c,String name,List<int[]> groups,int pb)throws Exception{
    try(PreparedStatement fa=c.prepareStatement("INSERT INTO fragment_atoms VALUES(?,?,?)");PreparedStatement v=c.prepareStatement("INSERT INTO versions VALUES(?,?,?,?,?)")){for(int f=0;f<groups.size();f++){TreeSet<Integer>b=new TreeSet<>();for(int a:groups.get(f)){fa.setString(1,name);fa.setInt(2,f);fa.setInt(3,a);fa.addBatch();b.addAll(UPDATES.get(a));}ArrayList<Integer> cuts=new ArrayList<>();cuts.add(0);cuts.addAll(b);cuts.add(T);for(int i=0;i<cuts.size()-1;i++){v.setString(1,name);v.setInt(2,f);v.setInt(3,cuts.get(i));v.setInt(4,cuts.get(i+1));v.setBytes(5,new byte[pb]);v.addBatch();}}fa.executeBatch();v.executeBatch();}
  }
  static double[] once(Connection c,String layout,int lo,int hi)throws Exception{
    ArrayList<Integer> fids=new ArrayList<>();String qi="SELECT DISTINCT fragment_id FROM fragment_atoms WHERE layout=? AND atom_id IN (?,?,?,?,?,?,?,?) ORDER BY fragment_id";
    long st=System.nanoTime();try(PreparedStatement p=c.prepareStatement(qi)){p.setString(1,layout);for(int i=0;i<8;i++)p.setInt(i+2,QUERY[i]);try(ResultSet r=p.executeQuery()){while(r.next())fids.add(r.getInt(1));}}
    StringBuilder in=new StringBuilder();for(int i=0;i<fids.size();i++){if(i>0)in.append(',');in.append('?');}long slices,bytes;try(PreparedStatement p=c.prepareStatement("SELECT COUNT(*),COALESCE(SUM(OCTET_LENGTH(payload)),0) FROM versions WHERE layout=? AND fragment_id IN ("+in+") AND valid_from<? AND valid_to>?")){p.setString(1,layout);int k=2;for(int f:fids)p.setInt(k++,f);p.setInt(k++,hi);p.setInt(k,lo);try(ResultSet r=p.executeQuery()){r.next();slices=r.getLong(1);bytes=r.getLong(2);}}
    long mid=System.nanoTime();double sum;try(PreparedStatement p=c.prepareStatement("SELECT SUM(value) FROM signal WHERE atom_id IN (?,?,?,?,?,?,?,?) AND t>=? AND t<?")){for(int i=0;i<8;i++)p.setInt(i+1,QUERY[i]);p.setInt(9,lo);p.setInt(10,hi);try(ResultSet r=p.executeQuery()){r.next();sum=r.getDouble(1);}}
    long end=System.nanoTime();return new double[]{(mid-st)/1e6,(end-st)/1e6,slices,bytes,sum};
  }
  public static void main(String[] args)throws Exception{Class.forName("org.hsqldb.jdbc.JDBCDriver");File out=new File(args.length>0?args[0]:".");out.mkdirs();try(Connection x=open("metadata")){DatabaseMetaData md=x.getMetaData();if(!"2.7.4".equals(md.getDatabaseProductVersion()))throw new RuntimeException("Refusing non-2.7.4 runtime: "+md.getDatabaseProductVersion());System.out.println("HSQLDB="+md.getDatabaseProductVersion());}
    try(PrintWriter w=new PrintWriter(new File(out,"RQ7_hsqldb274_raw.csv"))){w.println("run_id,payload_bytes,rep,query_id,layout,position,resolver_ms,full_ms,version_slices,payload_bytes_seen,signal_sum");for(int pb:PAYLOADS){Random rng=new Random(SEED+pb);int[] starts=new int[Q];for(int q=0;q<Q;q++)starts[q]=rng.nextInt(T-W);for(int run=0;run<RUNS;run++){try(Connection c=open("r"+pb+"_"+run)){setup(c,pb);for(int q=0;q<Q;q++)for(String l:new String[]{"static","temporal"})once(c,l,starts[q],starts[q]+W);for(int rep=0;rep<REPS;rep++)for(int q=0;q<Q;q++){String[] order=((rep+q)%2==0)?new String[]{"static","temporal"}:new String[]{"temporal","static"};for(int pos=0;pos<2;pos++){double[] z=once(c,order[pos],starts[q],starts[q]+W);w.printf(Locale.ROOT,"%d,%d,%d,%d,%s,%d,%.9f,%.9f,%.0f,%.0f,%.17g%n",run,pb,rep,q,order[pos],pos,z[0],z[1],z[2],z[3],z[4]);}}}System.out.println("completed payload="+pb+" run="+run);}}}
  }
}
